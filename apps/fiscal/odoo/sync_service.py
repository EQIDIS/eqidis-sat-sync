"""
Servicio de sincronización de CFDIs hacia Odoo.

Lógica principal para:
1. Buscar factura por UUID (método extendido para Odoo 18)
2. Verificar estado si existe
3. Crear factura con líneas si no existe
4. Crear ir.attachment con XML
5. Crear l10n_mx_edi.document para estados CFDI
6. Publicar factura automáticamente

Compatibilidad: Odoo 16, 17, 18
"""
import base64
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional
from django.utils import timezone

from apps.integrations.odoo.models import OdooConnection, OdooSyncLog
from .client import OdooClient, OdooClientError, create_client_from_connection

logger = logging.getLogger(__name__)


# Mapeo de estados SAT entre Django y Odoo
SAT_STATE_DJANGO_TO_ODOO = {
    'Vigente': 'valid',
    'Cancelado': 'cancelled',
    'No Encontrado': 'not_found',
    'Sin definir': 'not_defined',
    'Error': 'error',
    # Valores directos de Odoo
    'valid': 'valid',
    'cancelled': 'cancelled',
    'not_found': 'not_found',
    'not_defined': 'not_defined',
    'error': 'error',
    'skip': 'skip',
}

SAT_STATE_ODOO_TO_DJANGO = {
    'valid': 'Vigente',
    'cancelled': 'Cancelado',
    'not_found': 'No Encontrado',
    'not_defined': 'Sin definir',
    'error': 'Error',
    'skip': 'Skip',
}

# Mapeo de estados CFDI entre Django y Odoo
CFDI_STATE_DJANGO_TO_ODOO = {
    'draft': 'draft',
    'sent': 'invoice_sent',
    'cancel_requested': 'invoice_cancel_requested',
    'cancel': 'invoice_cancel',
    'received': 'invoice_received',
    'global_sent': 'ginvoice_sent',
    'global_cancel': 'ginvoice_cancel',
    # Valores de Odoo directos
    'invoice_sent': 'invoice_sent',
    'invoice_received': 'invoice_received',
    'invoice_cancel': 'invoice_cancel',
    'invoice_cancel_requested': 'invoice_cancel_requested',
}


def map_sat_state_to_odoo(django_state: str) -> str:
    """Convierte estado SAT de formato Django a formato Odoo."""
    return SAT_STATE_DJANGO_TO_ODOO.get(django_state, 'not_defined')


def map_sat_state_to_django(odoo_state: str) -> str:
    """Convierte estado SAT de formato Odoo a formato Django."""
    return SAT_STATE_ODOO_TO_DJANGO.get(odoo_state, 'Sin definir')


# Namespace del CFDI 4.0
CFDI_NS = {
    'cfdi': 'http://www.sat.gob.mx/cfd/4',
    'tfd': 'http://www.sat.gob.mx/TimbreFiscalDigital',
}


@dataclass
class CfdiLineItem:
    """Representa una línea/concepto del CFDI."""
    clave_prod_serv: str
    descripcion: str
    cantidad: Decimal
    clave_unidad: str
    unidad: str
    valor_unitario: Decimal
    importe: Decimal
    descuento: Decimal = Decimal('0')
    no_identificacion: str = ''  # NoIdentificacion del XML (código interno del proveedor)
    # Impuestos
    traslados: list = None  # [{'tipo': 'IVA', 'tasa': 0.16, 'importe': 100}]
    retenciones: list = None


@dataclass
class CfdiParsedData:
    """Datos parseados de un XML de CFDI."""
    uuid: str
    serie: str
    folio: str
    fecha: datetime
    fecha_timbrado: datetime  # Fecha del TimbreFiscalDigital
    forma_pago: str
    metodo_pago: str
    moneda: str
    tipo_cambio: Decimal
    tipo_comprobante: str
    subtotal: Decimal
    descuento: Decimal
    total: Decimal
    # Emisor
    rfc_emisor: str
    nombre_emisor: str
    regimen_fiscal_emisor: str
    # Receptor
    rfc_receptor: str
    nombre_receptor: str
    uso_cfdi: str
    # CFDI Relacionados (para l10n_mx_edi_cfdi_origin)
    cfdi_origin: str  # Formato: "<TipoRelacion>|<UUID1>,<UUID2>,..."
    # Líneas
    conceptos: list  # List[CfdiLineItem]


class CfdiXmlParser:
    """Parser de XML de CFDI 4.0."""
    
    @staticmethod
    def parse(xml_content: str) -> CfdiParsedData:
        """
        Parsea un XML de CFDI y extrae los datos relevantes.
        
        Args:
            xml_content: Contenido XML como string
            
        Returns:
            CfdiParsedData con los datos extraídos
        """
        root = ET.fromstring(xml_content)
        
        # Detectar namespace (CFDI 3.3 vs 4.0)
        ns = CFDI_NS
        if 'cfd/3' in root.tag:
            ns = {'cfdi': 'http://www.sat.gob.mx/cfd/3', 'tfd': CFDI_NS['tfd']}
        
        # Obtener UUID y FechaTimbrado del TimbreFiscalDigital
        tfd = root.find('.//tfd:TimbreFiscalDigital', ns)
        uuid = tfd.get('UUID') if tfd is not None else None
        fecha_timbrado_str = tfd.get('FechaTimbrado') if tfd is not None else None
        
        # Datos del comprobante
        comprobante = root
        
        # Emisor
        emisor = root.find('cfdi:Emisor', ns)
        
        # Receptor  
        receptor = root.find('cfdi:Receptor', ns)
        
        # Conceptos
        conceptos = []
        for concepto in root.findall('.//cfdi:Concepto', ns):
            traslados = []
            retenciones = []
            
            # Traslados del concepto
            for traslado in concepto.findall('.//cfdi:Traslado', ns):
                traslados.append({
                    'impuesto': traslado.get('Impuesto'),
                    'tipo_factor': traslado.get('TipoFactor'),
                    'tasa_o_cuota': Decimal(traslado.get('TasaOCuota', '0')),
                    'importe': Decimal(traslado.get('Importe', '0')),
                    'base': Decimal(traslado.get('Base', '0')),
                })
            
            # Retenciones del concepto
            for retencion in concepto.findall('.//cfdi:Retencion', ns):
                retenciones.append({
                    'impuesto': retencion.get('Impuesto'),
                    'tasa_o_cuota': Decimal(retencion.get('TasaOCuota', '0')),
                    'importe': Decimal(retencion.get('Importe', '0')),
                    'base': Decimal(retencion.get('Base', '0')),
                })
            
            line = CfdiLineItem(
                clave_prod_serv=concepto.get('ClaveProdServ', ''),
                descripcion=concepto.get('Descripcion', ''),
                cantidad=Decimal(concepto.get('Cantidad', '1')),
                clave_unidad=concepto.get('ClaveUnidad', ''),
                unidad=concepto.get('Unidad', ''),
                valor_unitario=Decimal(concepto.get('ValorUnitario', '0')),
                importe=Decimal(concepto.get('Importe', '0')),
                descuento=Decimal(concepto.get('Descuento', '0')),
                no_identificacion=concepto.get('NoIdentificacion', ''),
                traslados=traslados,
                retenciones=retenciones,
            )
            conceptos.append(line)
        
        # Parsear CfdiRelacionados → l10n_mx_edi_cfdi_origin
        cfdi_origin = ''
        cfdi_relacionados = root.find('cfdi:CfdiRelacionados', ns)
        if cfdi_relacionados is not None:
            tipo_relacion = cfdi_relacionados.get('TipoRelacion', '')
            uuids_relacionados = []
            for rel in cfdi_relacionados.findall('cfdi:CfdiRelacionado', ns):
                rel_uuid = rel.get('UUID', '')
                if rel_uuid:
                    uuids_relacionados.append(rel_uuid)
            if tipo_relacion and uuids_relacionados:
                cfdi_origin = f"{tipo_relacion}|{','.join(uuids_relacionados)}"

        # Parsear fecha de emisión
        fecha_emision = datetime.fromisoformat(comprobante.get('Fecha').replace('T', ' '))

        # Parsear fecha de timbrado (usar fecha de emisión si no existe)
        if fecha_timbrado_str:
            fecha_timbrado = datetime.fromisoformat(fecha_timbrado_str.replace('T', ' '))
        else:
            fecha_timbrado = fecha_emision

        return CfdiParsedData(
            uuid=uuid,
            serie=comprobante.get('Serie', ''),
            folio=comprobante.get('Folio', ''),
            fecha=fecha_emision,
            fecha_timbrado=fecha_timbrado,
            forma_pago=comprobante.get('FormaPago', ''),
            metodo_pago=comprobante.get('MetodoPago', ''),
            moneda=comprobante.get('Moneda', 'MXN'),
            tipo_cambio=Decimal(comprobante.get('TipoCambio', '1')),
            tipo_comprobante=comprobante.get('TipoDeComprobante', 'I'),
            subtotal=Decimal(comprobante.get('SubTotal', '0')),
            descuento=Decimal(comprobante.get('Descuento', '0')),
            total=Decimal(comprobante.get('Total', '0')),
            rfc_emisor=emisor.get('Rfc', '') if emisor is not None else '',
            nombre_emisor=emisor.get('Nombre', '') if emisor is not None else '',
            regimen_fiscal_emisor=emisor.get('RegimenFiscal', '') if emisor is not None else '',
            rfc_receptor=receptor.get('Rfc', '') if receptor is not None else '',
            nombre_receptor=receptor.get('Nombre', '') if receptor is not None else '',
            uso_cfdi=receptor.get('UsoCFDI', '') if receptor is not None else '',
            cfdi_origin=cfdi_origin,
            conceptos=conceptos,
        )


class OdooInvoiceSyncService:
    """
    Servicio para sincronizar CFDIs hacia Odoo.
    
    Flujo principal:
    1. Obtener XML del CFDI (desde ir.attachment en Odoo o S3)
    2. Parsear el XML para obtener líneas de detalle
    3. Buscar/crear partner por RFC
    4. Buscar/crear productos por ClaveProdServ
    5. Mapear impuestos
    6. Crear la factura en Odoo
    """
    
    def __init__(self, connection: OdooConnection):
        """
        Inicializa el servicio.
        
        Args:
            connection: Configuración de conexión a Odoo
        """
        self.connection = connection
        self.client: Optional[OdooClient] = None
    
    def _get_client(self) -> OdooClient:
        """Obtiene o crea el cliente Odoo."""
        if self.client is None:
            self.client = create_client_from_connection(self.connection)
        return self.client
    
    def sync_cfdi_to_odoo(self, cfdi_uuid: str, xml_content: str = None,
                          auto_post: bool = True) -> dict:
        """
        Sincroniza un CFDI hacia Odoo.

        Flujo completo para Odoo 18:
        1. Buscar factura existente por UUID (método extendido)
        2. Si no existe, crear factura con líneas
        3. Crear ir.attachment con XML del CFDI
        4. Crear l10n_mx_edi.document para estados (esto popula los campos computados)
        5. Publicar factura si auto_post=True

        Args:
            cfdi_uuid: UUID del CFDI a sincronizar
            xml_content: Contenido XML (requerido para crear factura)
            auto_post: Si True, publica la factura automáticamente (default: True)

        Returns:
            dict con resultado: {
                'status': 'created' | 'exists' | 'updated' | 'error',
                'odoo_invoice_id': int | None,
                'message': str
            }
        """
        client = self._get_client()
        company_id = self.connection.odoo_company_id

        # Crear log de sync
        sync_log = OdooSyncLog.objects.create(
            connection=self.connection,
            cfdi_uuid=cfdi_uuid,
            direction='to_odoo',
            status='pending'
        )

        try:
            # 1. Verificar si ya existe en Odoo (búsqueda extendida)
            existing_invoice = client.find_invoice_by_uuid_extended(cfdi_uuid, company_id)

            if existing_invoice:
                # Ya existe, verificar estado y reparar metadatos si es necesario
                result = self._handle_existing_invoice(existing_invoice, sync_log, xml_content)
                return result

            # 2. Verificar que tenemos XML
            if not xml_content:
                logger.info(f"XML no proporcionado para UUID {cfdi_uuid}")
                sync_log.status = 'error'
                sync_log.error_message = 'XML no disponible. Proporcione el contenido XML.'
                sync_log.completed_at = timezone.now()
                sync_log.save()
                return {
                    'status': 'error',
                    'odoo_invoice_id': None,
                    'message': 'XML no disponible'
                }

            # 3. Parsear el XML
            cfdi_data = CfdiXmlParser.parse(xml_content)

            # 4. Filtrar Tipos de Comprobante: Solo 'I' (Ingreso), 'E' (Egreso) y 'T' (Traslado)
            # Ignorar 'N' (Nómina) y 'P' (Pago / REP)
            if cfdi_data.tipo_comprobante in ('N', 'P'):
                logger.info(f"Saltando CFDI {cfdi_uuid} por ser tipo {cfdi_data.tipo_comprobante} (Nómina/Pagos)")
                sync_log.status = 'skipped'
                sync_log.error_message = f'Tipo de comprobante {cfdi_data.tipo_comprobante} ignorado.'
                sync_log.completed_at = timezone.now()
                sync_log.save()
                return {
                    'status': 'skipped',
                    'odoo_invoice_id': None,
                    'message': f'Tipo {cfdi_data.tipo_comprobante} ignorado.'
                }

            # --- NUEVO: Validar estado SAT real ---
            sat_status_raw = self._get_sat_status(cfdi_data)
            sat_status = sat_status_raw.get('estado', 'Vigente')

            # 5. Determinar tipo de factura
            move_type = self._get_move_type(cfdi_data, company_id)

            # 5. Buscar/crear partner
            partner_id = self._find_or_create_partner(cfdi_data, move_type, company_id)

            # 5.5 Segunda verificación anti-duplicado justo antes de crear
            # (protección contra condiciones de carrera en ejecución concurrente)
            recheck = client.find_invoice_by_uuid_extended(cfdi_uuid, company_id)
            if recheck:
                logger.info(f"UUID {cfdi_uuid} encontrado en re-verificación (race condition evitada)")
                result = self._handle_existing_invoice(recheck, sync_log, xml_content)
                return result

            # 6. Crear la factura Date-Driven (sin UUID - será computado)
            invoice_id = self._create_invoice_base(
                cfdi_data, partner_id, move_type, company_id
            )

            # 8. Buscar attachment existente o crear uno nuevo
            xml_base64 = base64.b64encode(xml_content.encode('utf-8')).decode('utf-8')
            uuid_upper = cfdi_data.uuid.upper()

            # Verificar si ya existe un attachment con este UUID PARA ESTA EMPRESA
            # (el mismo UUID puede existir en otra empresa legítimamente)
            existing_att = None
            try:
                existing_att = client.search_read(
                    'ir.attachment',
                    [
                        ['cfdi_uuid', 'in', [uuid_upper, cfdi_data.uuid.lower()]],
                        ['company_id', '=', company_id],
                    ],
                    fields=['id'],
                    limit=1
                )
            except Exception:
                pass

            if existing_att:
                attachment_id = existing_att[0]['id']
                logger.info(f"Attachment existente encontrado para UUID {cfdi_data.uuid}: ID={attachment_id}")
            else:
                cfdi_type_map = {
                    'out_invoice': 'I',
                    'in_invoice': 'SI',
                    'out_refund': 'E',
                    'in_refund': 'SE'
                }
                cfdi_attachment_type = cfdi_type_map.get(move_type, 'I')

                attachment_id = client.create_cfdi_attachment(
                    invoice_id=invoice_id,
                    xml_content_base64=xml_base64,
                    uuid=cfdi_data.uuid,
                    company_id=company_id,
                    cfdi_type=cfdi_attachment_type,
                    estado=sat_status
                )

            # 9. Vincular factura ↔ attachment (patrón IT Admin import_xml_file)
            # Paso A: Escribir AMBOS campos en la factura en un solo write
            #   attachment_id = campo custom Many2one del módulo ADD
            #   l10n_mx_edi_cfdi_attachment_id = campo estándar de l10n_mx_edi
            # Esto dispara _compute_cfdi_uuid en Odoo
            client.write('account.move', [invoice_id], {
                'attachment_id': attachment_id,
                'l10n_mx_edi_cfdi_attachment_id': attachment_id,
            })

            # Paso A.5: Verificación post-vinculación anti-duplicado
            # Ahora que el UUID está poblado, verificar que no se creó duplicado
            uuid_upper = cfdi_data.uuid.upper()
            uuid_lower = cfdi_data.uuid.lower()
            all_with_uuid = client.search_read(
                'account.move',
                [
                    '|',
                    ['l10n_mx_edi_cfdi_uuid', '=', uuid_upper],
                    ['l10n_mx_edi_cfdi_uuid', '=', uuid_lower],
                    ['company_id', '=', company_id],
                ],
                fields=['id'],
                limit=10
            )
            if len(all_with_uuid) > 1:
                # Hay duplicados - quedarnos con el de menor ID (el primero creado)
                existing_ids = sorted([inv['id'] for inv in all_with_uuid])
                if invoice_id != existing_ids[0]:
                    # Nosotros creamos el duplicado, eliminarlo
                    logger.warning(
                        f"Duplicado detectado post-creación para UUID {cfdi_uuid}: "
                        f"invoice_id={invoice_id} ya existía como {existing_ids[0]}. "
                        f"Eliminando duplicado."
                    )
                    try:
                        # Revertir a borrador antes de eliminar
                        client.write('account.move', [invoice_id], {'state': 'draft'})
                        client.execute_kw('account.move', 'unlink', [[invoice_id]])
                    except Exception as del_err:
                        logger.error(f"No se pudo eliminar duplicado {invoice_id}: {del_err}")

                    sync_log.status = 'success'
                    sync_log.odoo_invoice_id = existing_ids[0]
                    sync_log.action_taken = 'duplicate_prevented'
                    sync_log.completed_at = timezone.now()
                    sync_log.save()
                    return {
                        'status': 'exists',
                        'odoo_invoice_id': existing_ids[0],
                        'message': f'Duplicado detectado y eliminado. Factura existente: {existing_ids[0]}'
                    }

            # Paso B: Actualizar attachment - vincular via invoice_ids y limpiar res_id/res_model
            # (patrón IT Admin: val.update({'invoice_ids': [(4, res_id)], 'res_id': False, 'res_model': False}))
            try:
                client.write('ir.attachment', [attachment_id], {
                    'invoice_ids': [(4, invoice_id)],
                    'res_id': False,
                    'res_model': False,
                    'creado_en_odoo': True,
                })
            except Exception as e:
                logger.warning(f"No se pudo actualizar campos custom del attachment {attachment_id}: {e}")
                # Fallback: limpiar solo campos estándar (invoice_ids/creado_en_odoo pueden no existir)
                client.write('ir.attachment', [attachment_id], {
                    'res_id': 0,
                    'res_model': False,
                })

            # Paso C: Crear l10n_mx_edi.document con invoice_ids (campo crítico para Tab CFDI)
            cfdi_state = self._get_cfdi_state(move_type)
            cfdi_datetime = cfdi_data.fecha_timbrado.strftime('%Y-%m-%d %H:%M:%S')
            doc_id = None

            # Verificar si ya fue creado por el compute de Odoo
            existing_docs = client.search_read(
                'l10n_mx_edi.document',
                [['move_id', '=', invoice_id]],
                fields=['id'],
                limit=1
            )
            if not existing_docs:
                doc_id = client.create_l10n_mx_edi_document(
                    invoice_id=invoice_id,
                    attachment_id=attachment_id,
                    state=cfdi_state,
                    sat_state='not_defined',
                    cfdi_datetime=cfdi_datetime
                )
            else:
                doc_id = existing_docs[0]['id']

            # 10. Publicar factura si se solicita
            if auto_post:
                client.post_invoice(invoice_id)

            # Actualizar log
            sync_log.status = 'success'
            sync_log.odoo_invoice_id = invoice_id
            sync_log.action_taken = 'created'
            sync_log.response_data = {
                'attachment_id': attachment_id,
                'edi_document_id': doc_id,
                'posted': auto_post
            }
            sync_log.completed_at = timezone.now()
            sync_log.save()

            logger.info(
                f"CFDI {cfdi_uuid} sincronizado: invoice={invoice_id}, "
                f"attachment={attachment_id}, edi_doc={doc_id}"
            )

            return {
                'status': 'created',
                'odoo_invoice_id': invoice_id,
                'message': f'Factura creada exitosamente con ID {invoice_id}'
            }

        except Exception as e:
            logger.exception(f"Error sincronizando CFDI {cfdi_uuid}")
            sync_log.status = 'error'
            sync_log.error_message = str(e)
            sync_log.completed_at = timezone.now()
            sync_log.save()

            # NO desactivar la conexión por errores individuales
            self.connection.last_error = str(e)
            self.connection.save(update_fields=['last_error'])

            return {
                'status': 'error',
                'odoo_invoice_id': None,
                'message': str(e)
            }

    def _get_cfdi_state(self, move_type: str) -> str:
        """
        Determina el estado del CFDI según el tipo de factura.

        Args:
            move_type: Tipo de movimiento en Odoo

        Returns:
            Estado para l10n_mx_edi.document
        """
        # El módulo IT Admin siempre usa 'invoice_sent' - es el estado válido
        # en Odoo 17/18 para l10n_mx_edi.document. 'invoice_received' no existe
        # como state válido y causa que el create falle silenciosamente.
        return 'invoice_sent'
    
    def _handle_existing_invoice(self, invoice: dict, sync_log: OdooSyncLog, 
                                xml_content: str = None) -> dict:
        """
        Maneja el caso cuando la factura ya existe en Odoo.

        Verifica el estado actual y repara metadatos (vínculo de attachment) si falta.
        """
        client = self._get_client()
        invoice_id = invoice['id']
        company_id = self.connection.odoo_company_id
        
        # Obtener información de estados CFDI si están disponibles
        cfdi_uuid = invoice.get('l10n_mx_edi_cfdi_uuid', 'N/A')
        cfdi_state = invoice.get('l10n_mx_edi_cfdi_state', 'N/A')
        sat_state = invoice.get('l10n_mx_edi_cfdi_sat_state', 'N/A')
        
        # Reparar metadatos: Verificar si tiene el attachment vinculado (campo attachment_id del módulo ADD)
        # Odoo search_read nos devolvió la factura con campos extendidos si existen.
        current_att_id = invoice.get('attachment_id')
        if isinstance(current_att_id, (list, tuple)):
            current_att_id = current_att_id[0]
            
        action_taken = 'verified'
        
        if not current_att_id and xml_content:
            # No tiene el vínculo técnico o está vacío (Metadata 0.00 en Odoo)
            logger.info(f"Reparando metadatos para factura existente {invoice_id} ({cfdi_uuid})")
            
            # Buscar si ya existe un adjunto XML para esta factura
            existing_att = client.find_attachment_by_res_id('account.move', invoice_id, f"{cfdi_uuid}.xml")
            
            if existing_att and not existing_att.get('cfdi_total'):
                # Existe pero está vacío (Importe 0.00), lo borramos para re-crearlo con contexto
                try:
                    client.execute_kw('ir.attachment', 'unlink', [[existing_att['id']]])
                    existing_att = None
                except Exception: pass
                
            if not existing_att:
                # Re-crear adjunto con el contexto 'is_fiel_attachment'
                xml_base64 = base64.b64encode(xml_content.encode('utf-8')).decode('utf-8')
                move_type = invoice.get('move_type', 'out_invoice')
                cfdi_type_map = {'out_invoice': 'I', 'in_invoice': 'SI', 'out_refund': 'E', 'in_refund': 'SE'}
                cfdi_type = cfdi_type_map.get(move_type, 'I')
                
                try:
                    current_att_id = client.create_cfdi_attachment(
                        invoice_id=invoice_id,
                        xml_content_base64=xml_base64,
                        uuid=cfdi_uuid,
                        company_id=company_id,
                        cfdi_type=cfdi_type,
                        estado='Vigente'
                    )
                    action_taken = 'metadata_repaired'
                except Exception as e:
                    logger.warning(f"Error re-creando attachment para {cfdi_uuid}: {e}")
            else:
                current_att_id = existing_att['id']
                
            # Forzar el vínculo técnico en account.move
            if current_att_id:
                try:
                    client.update_invoice_attachment_link(invoice_id, current_att_id)
                except Exception as e:
                    logger.warning(f"Error vinculando invoice {invoice_id} a att {current_att_id}: {e}")

        sync_log.status = 'success'
        sync_log.odoo_invoice_id = invoice_id
        sync_log.action_taken = action_taken
        sync_log.response_data = {
            'invoice': invoice,
            'cfdi_uuid': cfdi_uuid,
            'cfdi_state': cfdi_state,
            'sat_state': sat_state,
            'attachment_linked': bool(current_att_id)
        }
        sync_log.completed_at = timezone.now()
        sync_log.save()

        message = (
            f"Factura ya existe: {invoice.get('name', 'Sin nombre')} "
            f"(estado: {invoice.get('state')}, CFDI: {cfdi_state}, SAT: {sat_state})"
        )
        if action_taken == 'metadata_repaired':
            message += " [Metadatos reparados]"

        sync_log.completed_at = timezone.now()
        sync_log.save()

        message = (
            f"Factura ya existe: {invoice.get('name', 'Sin nombre')} "
            f"(estado: {invoice.get('state')}, CFDI: {cfdi_state}, SAT: {sat_state})"
        )

        logger.info(f"CFDI {cfdi_uuid} ya existe en Odoo como factura {invoice['id']}")

        return {
            'status': 'exists',
            'odoo_invoice_id': invoice['id'],
            'message': message,
            'cfdi_state': cfdi_state,
            'sat_state': sat_state,
        }
    
    def _get_move_type(self, cfdi_data: CfdiParsedData, company_id: int) -> str:
        """
        Determina el tipo de movimiento en Odoo.
        
        Si el RFC emisor es de nuestra empresa → out_invoice (factura de venta)
        Si el RFC receptor es de nuestra empresa → in_invoice (factura de compra)
        """
        client = self._get_client()
        
        # Obtener RFC de nuestra empresa
        companies = client.search_read(
            'res.company',
            [['id', '=', company_id]],
            fields=['vat']
        )
        our_vat = companies[0]['vat'] if companies else None
        
        if our_vat:
            if cfdi_data.rfc_emisor.upper() == our_vat.upper():
                return 'out_invoice'  # Nosotros emitimos
            elif cfdi_data.rfc_receptor.upper() == our_vat.upper():
                return 'in_invoice'  # Nosotros recibimos
        
        # Default: factura de proveedor (recibida)
        return 'in_invoice'
    
    def _find_or_create_partner(self, cfdi_data: CfdiParsedData, 
                                 move_type: str, company_id: int) -> int:
        """
        Busca o crea el partner (cliente/proveedor).
        
        Siguiendo el patrón del módulo antiguo:
        - Buscar por VAT (RFC)
        - Crear con company_id específico
        - Agregar customer_rank o supplier_rank según el tipo de factura
        """
        client = self._get_client()
        
        # Determinar cuál RFC buscar y si es cliente o proveedor
        if move_type in ('out_invoice', 'out_refund'):
            # Factura de venta: buscar al receptor (cliente)
            vat = cfdi_data.rfc_receptor
            name = cfdi_data.nombre_receptor
            is_customer = True
            is_supplier = False
        else:
            # Factura de compra: buscar al emisor (proveedor)
            vat = cfdi_data.rfc_emisor
            name = cfdi_data.nombre_emisor
            is_customer = False
            is_supplier = True
        
        # Buscar partner existente
        partner = client.find_partner_by_vat(vat, company_id)
        if partner:
            return partner['id']
        
        # Crear nuevo partner con company_id y ranks
        partner_vals = {
            'name': name or vat,
            'vat': vat,
            'company_type': 'company' if len(vat) == 12 else 'person',
            'is_company': len(vat) == 12,
            'country_id': 156,  # México
            'company_id': company_id,
        }
        
        # Agregar customer/supplier rank según el tipo
        if is_customer:
            partner_vals['customer_rank'] = 1
        if is_supplier:
            partner_vals['supplier_rank'] = 1
        
        partner_id = client.create('res.partner', partner_vals)
        logger.info(f"Partner creado: ID={partner_id}, VAT={vat}, customer={is_customer}, supplier={is_supplier}")
        return partner_id

    def _find_or_create_product(self, concepto: CfdiLineItem, company_id: int,
                                is_supplier: bool = True) -> Optional[int]:
        """
        Busca o crea un producto siguiendo el patrón multi-etapa del módulo IT Admin:
        1. Buscar por NoIdentificacion (default_code) - código interno del proveedor
        2. Buscar en product.supplierinfo por product_code
        3. Buscar por nombre exacto del producto
        4. Crear producto nuevo si no se encuentra

        IMPORTANTE: NO busca por ClaveProdServ (código SAT genérico) porque es
        compartido por muchos productos distintos y causa matches incorrectos.
        """
        client = self._get_client()
        company_ids = [company_id, False]
        no_identificacion = concepto.no_identificacion
        product_name = concepto.descripcion or concepto.clave_prod_serv or ''

        # Etapa 1: Buscar por default_code = NoIdentificacion (código interno, NO clave SAT)
        if no_identificacion:
            products = client.search_read(
                'product.product',
                [['default_code', '=', no_identificacion], ['company_id', 'in', company_ids]],
                fields=['id'], limit=1
            )
            if products:
                return products[0]['id']

            # Etapa 1b: Buscar en product.supplierinfo por product_code
            try:
                supplier_infos = client.search_read(
                    'product.supplierinfo',
                    [['product_code', '=', no_identificacion], ['company_id', 'in', company_ids]],
                    fields=['product_tmpl_id'], limit=1
                )
                if supplier_infos and supplier_infos[0].get('product_tmpl_id'):
                    tmpl_id = supplier_infos[0]['product_tmpl_id'][0]
                    variants = client.search_read(
                        'product.product',
                        [['product_tmpl_id', '=', tmpl_id]],
                        fields=['id'], limit=1
                    )
                    if variants:
                        return variants[0]['id']
            except Exception:
                pass

        # Etapa 2: Buscar por nombre exacto del producto
        if product_name:
            products = client.search_read(
                'product.product',
                [['name', '=', product_name], ['company_id', 'in', company_ids]],
                fields=['id'], limit=1
            )
            if products:
                return products[0]['id']

        # Etapa 3: Crear producto nuevo
        try:
            product_vals = {
                'name': product_name,
                'type': 'consu',
                'company_id': company_id,
                'sale_ok': not is_supplier,
                'purchase_ok': is_supplier,
            }
            if no_identificacion:
                product_vals['default_code'] = no_identificacion
            return client.create('product.product', product_vals)
        except Exception as e:
            logger.warning(f"Error creando producto '{product_name}': {e}")
            return None

    def _find_or_create_tax(self, client: 'OdooClient', tasa_pct: float, tax_type: str, 
                            company_id: int, sat_code: str, factor_type: str) -> Optional[int]:
        tax = client.find_tax_extended(tasa_pct, tax_type, company_id, sat_code, factor_type)
        if tax:
            return tax['id']
            
        try:
            return client.create('account.tax', {
                'name': f"{sat_code} {tasa_pct}% {tax_type}",
                'amount_type': 'percent',
                'amount': tasa_pct,
                'type_tax_use': tax_type,
                'company_id': company_id,
            })
        except Exception as e:
            logger.warning(f"Error creando impuesto genérico {sat_code} {tasa_pct}%: {e}")
            return None
    
    # Constants for IEPS Whitelist (Fuel, Junk Food, etc.) from ADD module
    IEPS_WHITELIST = {
        '50202200', '50202201', '50202202', '50202203', '50202204', '50202205', '50202206',
        '50202207', '50202208', '50202209', '50202210', '90101504', '90101502', '73131503',
        '50211500', '50211502', '50211503', '50211504', '50211506', '70141519', '15101505',
        '15101513', '15101514', '15101515', '50202303', '50202304', '50202306', '50202307',
        '50202308', '50202309', '50202311', '73131504', '50131700', '50131701', '50131702',
        '50131703', '50161800', '50161813', '50161814', '50161815', '50161500', '50161509',
        '50161510', '50161511', '50161512', '50192100', '50192109', '50192300', '50192301',
        '50192302', '50192303', '50192304', '50192400', '50192401', '50192403', '50192404',
        '50192405', '50192406', '50193100', '50193101', '50193102', '73131906', '73131903',
        '15101510', '15101511', '15101512' # Gasolina Magna, Premium, Diesel
    }

    def _quantize(self, amount: Decimal) -> Decimal:
        from decimal import ROUND_HALF_UP
        return amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    def _create_invoice_base(self, cfdi_data: CfdiParsedData, partner_id: int,
                             move_type: str, company_id: int) -> int:
        """
        Crea la factura base en Odoo usando un enfoque Data-Driven (como ADD).
        Construye manualmente los apuntes de product, tax, y payment_term.
        """
        client = self._get_client()
        is_supplier = move_type in ('in_invoice', 'in_refund')
        tax_type = 'purchase' if is_supplier else 'sale'

        currencies = client.search_read('res.currency', [['name', '=', cfdi_data.moneda]], fields=['id', 'name'])
        currency = currencies[0] if currencies else None

        company = client.search_read('res.company', [['id', '=', company_id]], fields=['currency_id'])
        company_currency_id = company[0]['currency_id'][0] if company and company[0].get('currency_id') else None
        
        has_foreign_currency = currency and currency['id'] != company_currency_id
        sign_base = 1 if move_type in ('in_invoice', 'out_refund') else -1
        tipo_cambio = cfdi_data.tipo_cambio or Decimal('1')
        
        journals = client.search_read('account.journal', 
            [['type', '=', 'purchase' if is_supplier else 'sale'], ['company_id', '=', company_id]], 
            limit=1, fields=['id', 'default_account_id'])
        journal_id = journals[0]['id'] if journals else None

        move_lines = []

        
        # 1. Procesar Conceptos
        for concepto in cfdi_data.conceptos:
            neto_xml = concepto.importe - concepto.descuento
            product_id = self._find_or_create_product(concepto, company_id, is_supplier)
            account_id = client.get_product_account(product_id, is_supplier, journal_id) if product_id else None
            if not account_id and journals and journals[0].get('default_account_id'):
                account_id = journals[0]['default_account_id'][0]

            amount_company = self._quantize((neto_xml * tipo_cambio) if has_foreign_currency else neto_xml)
            balance = amount_company * sign_base
            
            amount_currency = self._quantize(neto_xml * sign_base if has_foreign_currency else balance)

            iva_tras = next((t for t in (concepto.traslados or []) if t.get('impuesto') == '002'), None)
            ieps_tras = next((t for t in (concepto.traslados or []) if t.get('impuesto') == '003'), None)
            
            ieps_inferido = Decimal('0')
            base_iva = Decimal('0')
            if iva_tras:
                base_iva = Decimal(str(iva_tras.get('base', '0')))
                if base_iva < neto_xml and not ieps_tras and concepto.clave_prod_serv in self.IEPS_WHITELIST:
                    ieps_inferido = neto_xml - base_iva

            all_taxes_xml = (concepto.traslados or []) + (concepto.retenciones or [])
            tax_ids_base = []
            
            for tax in all_taxes_xml:
                tasa_pct = (tax['tasa_o_cuota'] * 100) if tax in (concepto.traslados or []) else (tax['tasa_o_cuota'] * -100)
                if tasa_pct != 0:
                    t_id = self._find_or_create_tax(client, float(tasa_pct), tax_type, company_id, tax.get('impuesto'), tax.get('tipo_factor', 'Tasa'))
                    if t_id:
                        tax_ids_base.append(t_id)

            def create_line_vals(name, amount_val, t_ids, is_ieps=False):
                amt_comp = self._quantize((amount_val * tipo_cambio) if has_foreign_currency else amount_val)
                bal = amt_comp * sign_base
                amt_curr = self._quantize(amount_val * sign_base if has_foreign_currency else bal)
                return {
                    'name': name,
                    'account_id': account_id,
                    'product_id': product_id,
                    'quantity': 1.0,
                    'price_unit': float(self._quantize(amount_val)),
                    'balance': float(bal),
                    'debit': float(bal) if bal > 0 else 0.0,
                    'credit': float(-bal) if bal < 0 else 0.0,
                    'currency_id': currency['id'] if currency else None,
                    'amount_currency': float(amt_curr),
                    'partner_id': partner_id,
                    'display_type': 'product',
                    'tax_ids': [(6, 0, t_ids)] if t_ids else [],
                }
            
            if ieps_inferido > 0:
                base_line = create_line_vals(f"[{concepto.clave_prod_serv}] {concepto.descripcion}", base_iva, tax_ids_base)
                move_lines.append((0, 0, base_line))
                ieps_line = create_line_vals(f"[{concepto.clave_prod_serv}] {concepto.descripcion} (IEPS implícito)", ieps_inferido, [], True)
                move_lines.append((0, 0, ieps_line))
            else:
                prod_line = create_line_vals(f"[{concepto.clave_prod_serv}] {concepto.descripcion}", neto_xml, tax_ids_base)
                move_lines.append((0, 0, prod_line))
        
        # 2. Procesar Impuestos (Taxes Consolidados) - siguiendo patrón ADD module
        # Acumular por tax_id como lo hace ADD
        tax_groups = {}        # tax_id -> signed_company_amount
        tax_currency_groups = {}  # tax_id -> signed_currency_amount
        tax_base_groups = {}  # tax_id -> base_amount_company
        tax_entries = []      # lista de {'tax_id', 'tax_amount', 'base_amount'} para tax_totals
        tax_summary = {}      # para mantener compatibilidad con _generate_tax_totals

        for concepto in cfdi_data.conceptos:
            for tras in (concepto.traslados or []):
                tasa_pct = float(tras['tasa_o_cuota'] * 100)
                if tasa_pct == 0:
                    continue
                tax_id = self._find_or_create_tax(client, tasa_pct, tax_type, company_id, tras.get('impuesto'), tras.get('tipo_factor', 'Tasa'))
                if not tax_id:
                    continue

                amount_tax = tras['importe']
                base_tax = tras.get('base', Decimal('0'))
                amount_company_tax = self._quantize((amount_tax * tipo_cambio) if has_foreign_currency else amount_tax)

                signed_company = amount_company_tax * sign_base
                signed_currency = self._quantize(amount_tax * sign_base if has_foreign_currency else signed_company)

                tax_groups[tax_id] = tax_groups.get(tax_id, Decimal('0')) + signed_company
                tax_currency_groups[tax_id] = tax_currency_groups.get(tax_id, Decimal('0')) + signed_currency
                if base_tax:
                    base_company = self._quantize((base_tax * tipo_cambio) if has_foreign_currency else base_tax)
                    tax_base_groups[tax_id] = tax_base_groups.get(tax_id, Decimal('0')) + base_company
                tax_entries.append({'tax_id': tax_id, 'tax_amount': signed_company, 'base_amount': base_company if base_tax else Decimal('0')})

                # Mantener tax_summary para _generate_tax_totals
                key = f"TRAS_{tras['impuesto']}_{tras['tasa_o_cuota']}"
                if key not in tax_summary:
                    t_record = client.find_tax_extended(tasa_pct, tax_type, company_id, tras.get('impuesto'), tras.get('tipo_factor', 'Tasa'))
                    group_id = False
                    group_name = f"Impuesto {tras['impuesto']}"
                    if t_record and t_record.get('tax_group_id'):
                        group_id = t_record['tax_group_id'][0]
                        group_name = t_record['tax_group_id'][1]
                    tax_summary[key] = {
                        'type': 'traslado', 'impuesto': tras['impuesto'],
                        'tasa': tras['tasa_o_cuota'], 'importe': Decimal('0'),
                        'group_id': group_id, 'group_name': group_name
                    }
                tax_summary[key]['importe'] += tras['importe']

            for ret in (concepto.retenciones or []):
                tasa_pct = float(ret['tasa_o_cuota'] * -100)
                if tasa_pct == 0:
                    continue
                tax_id = self._find_or_create_tax(client, tasa_pct, tax_type, company_id, ret.get('impuesto'), 'Tasa')
                if not tax_id:
                    continue

                amount_tax = ret['importe']
                base_tax = ret.get('base', Decimal('0'))
                amount_company_tax = self._quantize((amount_tax * tipo_cambio) if has_foreign_currency else amount_tax)

                # Retenciones: sign_base * -1 (como ADD module)
                signed_company = amount_company_tax * sign_base * Decimal('-1')
                signed_currency = self._quantize(amount_tax * sign_base * Decimal('-1') if has_foreign_currency else signed_company)

                tax_groups[tax_id] = tax_groups.get(tax_id, Decimal('0')) + signed_company
                tax_currency_groups[tax_id] = tax_currency_groups.get(tax_id, Decimal('0')) + signed_currency
                if base_tax:
                    base_company = self._quantize((base_tax * tipo_cambio) if has_foreign_currency else base_tax)
                    tax_base_groups[tax_id] = tax_base_groups.get(tax_id, Decimal('0')) + base_company
                tax_entries.append({'tax_id': tax_id, 'tax_amount': signed_company, 'base_amount': base_company if base_tax else Decimal('0')})

                key = f"RET_{ret['impuesto']}_{ret['tasa_o_cuota']}"
                if key not in tax_summary:
                    t_record = client.find_tax_extended(tasa_pct, tax_type, company_id, ret.get('impuesto'), 'Tasa')
                    group_id = False
                    group_name = f"Retención {ret['impuesto']}"
                    if t_record and t_record.get('tax_group_id'):
                        group_id = t_record['tax_group_id'][0]
                        group_name = t_record['tax_group_id'][1]
                    tax_summary[key] = {
                        'type': 'retencion', 'impuesto': ret['impuesto'],
                        'tasa': ret['tasa_o_cuota'], 'importe': Decimal('0'),
                        'group_id': group_id, 'group_name': group_name
                    }
                tax_summary[key]['importe'] += ret['importe']

        # Crear líneas de impuestos consolidadas por tax_id (como ADD module)
        for tax_id, tax_amount_company in tax_groups.items():
            if tax_amount_company == 0:
                continue

            amount_currency_tax = tax_currency_groups.get(tax_id, Decimal('0'))
            base_amount = tax_base_groups.get(tax_id, Decimal('0'))

            # Obtener cuenta y repartition line del impuesto
            tax_account_id = None
            tax_repartition_line_id = False
            tax_name = f"Impuesto {tax_id}"

            if tax_id:
                # Leer el nombre del impuesto
                try:
                    tax_data = client.read('account.tax', [tax_id], ['name'])
                    if tax_data:
                        tax_name = tax_data[0].get('name', tax_name)
                except Exception:
                    pass

                # Buscar repartition line (invoice o refund según move_type)
                rep_type_filter = 'invoice' if move_type in ('in_invoice', 'out_invoice') else 'refund'
                reps = client.search_read('account.tax.repartition.line',
                    [['tax_id', '=', tax_id], ['repartition_type', '=', 'tax'],
                     ['document_type', '=', rep_type_filter]],
                    fields=['account_id', 'id'], limit=1)
                if not reps:
                    # Fallback sin filtro de document_type
                    reps = client.search_read('account.tax.repartition.line',
                        [['tax_id', '=', tax_id], ['repartition_type', '=', 'tax']],
                        fields=['account_id', 'id'], limit=1)
                if reps:
                    tax_repartition_line_id = reps[0]['id']
                    if reps[0].get('account_id'):
                        tax_account_id = reps[0]['account_id'][0]

            if not tax_account_id and journals and journals[0].get('default_account_id'):
                tax_account_id = journals[0]['default_account_id'][0]

            tax_line = {
                'name': tax_name,
                'account_id': tax_account_id,
                'balance': float(tax_amount_company),
                'debit': float(tax_amount_company) if tax_amount_company > 0 else 0.0,
                'credit': float(-tax_amount_company) if tax_amount_company < 0 else 0.0,
                'currency_id': currency['id'] if currency else None,
                'amount_currency': float(amount_currency_tax),
                'display_type': 'tax',
                'tax_line_id': tax_id,
                'tax_repartition_line_id': tax_repartition_line_id,
                'tax_base_amount': float(base_amount),
                'partner_id': partner_id,
            }
            move_lines.append((0, 0, tax_line))

        # 3. Línea Contrapartida (Receivable/Payable) - Patrón ADD Module
        payable_account_id = client.get_partner_account(partner_id, is_supplier)
        if not payable_account_id:
            logger.warning(f"Partner {partner_id} sin cuenta CxC/CxP, usando diario por defecto.")
            payable_account_id = journal_id

        # Calcular balance exacto desde las líneas existentes (como ADD module)
        total_balance = sum([Decimal(str(l[2].get('debit', 0))) - Decimal(str(l[2].get('credit', 0))) for l in move_lines])
        total_amount_currency = sum([Decimal(str(l[2].get('amount_currency', 0))) for l in move_lines])

        balance_counter = self._quantize(-total_balance)
        amount_currency_counter = -total_amount_currency if has_foreign_currency else balance_counter
        amount_currency_counter = self._quantize(amount_currency_counter)

        # Micro-ajuste final para garantizar balance cero (como ADD module)
        final_balance = total_balance + balance_counter
        if final_balance != 0:
            balance_counter = self._quantize(balance_counter - final_balance)

        counterpart_line = {
            'account_id': payable_account_id,
            'balance': float(balance_counter),
            'debit': float(balance_counter) if balance_counter > 0 else 0.0,
            'credit': float(-balance_counter) if balance_counter < 0 else 0.0,
            'currency_id': currency['id'] if currency else None,
            'amount_currency': float(amount_currency_counter),
            'partner_id': partner_id,
            'display_type': 'payment_term',
        }

        # Float-level balance fix (protects against binary float drift - ADD module)
        temp_lines = move_lines + [(0, 0, counterpart_line)]
        float_balance = sum([Decimal(str(l[2].get('debit', 0))) - Decimal(str(l[2].get('credit', 0))) for l in temp_lines])
        float_balance = self._quantize(float_balance)
        if float_balance != 0:
            if float_balance > 0:
                counterpart_line['credit'] = float(Decimal(str(counterpart_line['credit'])) + float_balance)
                counterpart_line['balance'] = float(Decimal(str(counterpart_line['balance'])) - float_balance)
            else:
                counterpart_line['debit'] = float(Decimal(str(counterpart_line['debit'])) + (-float_balance))
                counterpart_line['balance'] = float(Decimal(str(counterpart_line['balance'])) + (-float_balance))

        move_lines.append((0, 0, counterpart_line))

        # 5. Generar tax_totals JSON (patrón ADD module con sign_base)
        tax_totals = self._generate_tax_totals_v2(
            cfdi_data, tax_entries, tax_type, currency, company_currency_id, sign_base, client
        )

        ref = f"{cfdi_data.serie}{cfdi_data.folio}" if cfdi_data.serie else (cfdi_data.folio or '')

        # 6. Buscar payment_method de l10n_mx_edi (como ADD module)
        payment_method_id = False
        if cfdi_data.forma_pago:
            payment_method_id = self._find_payment_method(client, cfdi_data.forma_pago)

        invoice_vals = {
            'move_type': move_type,
            'partner_id': partner_id,
            'company_id': company_id,
            'invoice_date': cfdi_data.fecha.strftime('%Y-%m-%d'),
            'ref': ref,
            'payment_reference': ref if is_supplier else False,
            'journal_id': journal_id,
            'line_ids': move_lines,
            'tax_totals': tax_totals,
            # Campos CFDI (como ADD module)
            'l10n_mx_edi_usage': cfdi_data.uso_cfdi or False,
            'l10n_mx_edi_payment_policy': cfdi_data.metodo_pago or False,
            'l10n_mx_edi_cfdi_uuid_cusom': cfdi_data.uuid or False,
            'l10n_mx_edi_cfdi_origin': cfdi_data.cfdi_origin or False,
        }

        if payment_method_id:
            invoice_vals['l10n_mx_edi_payment_method_id'] = payment_method_id

        if currency:
            invoice_vals['currency_id'] = currency['id']

        if cfdi_data.forma_pago:
            invoice_vals['narration'] = f"Forma de pago: {cfdi_data.forma_pago}"
        if cfdi_data.metodo_pago:
            narration = invoice_vals.get('narration', '')
            invoice_vals['narration'] = f"{narration} Método de pago: {cfdi_data.metodo_pago}".strip()

        # Usar context check_move_validity=False como el módulo ADD
        invoice_id = client.create('account.move', invoice_vals, context={'check_move_validity': False})
        logger.info(f"Factura Data-Driven creada en Odoo: ID={invoice_id}, ref={ref}")

        return invoice_id

    def _generate_tax_totals(self, cfdi_data, tax_summary, currency, company_currency_id, sign_base):
        """Genera el diccionario tax_totals - LEGACY, usar _generate_tax_totals_v2."""
        return self._generate_tax_totals_v2(cfdi_data, [], 'purchase', currency, company_currency_id, sign_base, None)

    def _generate_tax_totals_v2(self, cfdi_data, tax_entries, tax_type, currency, company_currency_id, sign_base, client):
        """
        Genera tax_totals siguiendo el patrón exacto del módulo ADD.
        Agrupa por tax_group_id y aplica sign_base a los montos.
        """
        currency_rounding = currency.get('rounding', 0.01) if currency else 0.01
        subtotal_xml = cfdi_data.subtotal - cfdi_data.descuento
        base_amount_total = float(subtotal_xml)

        tax_groups_payload = []
        tax_amount_total = Decimal('0')
        group_map = {}

        for entry in tax_entries:
            tax_id = entry['tax_id']
            # Obtener tax_group_id del impuesto
            group_id = False
            group_name = 'Impuestos'
            if client and tax_id:
                try:
                    tax_data = client.read('account.tax', [tax_id], ['tax_group_id', 'name'])
                    if tax_data and tax_data[0].get('tax_group_id'):
                        group_id = tax_data[0]['tax_group_id'][0]
                        group_name = tax_data[0]['tax_group_id'][1]
                except Exception:
                    pass

            key = group_id or tax_id
            if key not in group_map:
                group_map[key] = {
                    'id': group_id,
                    'involved_tax_ids': [tax_id],
                    'tax_amount_currency': Decimal('0'),
                    'tax_amount': Decimal('0'),
                    'base_amount_currency': Decimal('0'),
                    'base_amount': Decimal('0'),
                    'display_base_amount_currency': Decimal('0'),
                    'display_base_amount': Decimal('0'),
                    'group_name': group_name,
                    'group_label': False,
                }
            group_map[key]['tax_amount'] += entry['tax_amount']
            group_map[key]['tax_amount_currency'] += entry['tax_amount']
            group_map[key]['base_amount'] += entry['base_amount']
            group_map[key]['base_amount_currency'] += entry['base_amount']
            group_map[key]['display_base_amount'] += entry['base_amount']
            group_map[key]['display_base_amount_currency'] += entry['base_amount']

        for g in group_map.values():
            tax_amount_total += g['tax_amount']
            tax_groups_payload.append({
                'id': g['id'],
                'involved_tax_ids': g['involved_tax_ids'],
                'tax_amount_currency': float(g['tax_amount_currency'] * sign_base),
                'tax_amount': float(g['tax_amount'] * sign_base),
                'base_amount_currency': float(g['base_amount_currency'] * sign_base),
                'base_amount': float(g['base_amount'] * sign_base),
                'display_base_amount_currency': float(g['display_base_amount_currency'] * sign_base),
                'display_base_amount': float(g['display_base_amount'] * sign_base),
                'group_name': g['group_name'],
                'group_label': g['group_label'],
            })

        return {
            'currency_id': currency['id'] if currency else company_currency_id,
            'currency_pd': currency_rounding,
            'company_currency_id': company_currency_id,
            'company_currency_pd': 0.01,
            'has_tax_groups': True,
            'subtotals': [{
                'tax_groups': tax_groups_payload,
                'tax_amount_currency': float(tax_amount_total * sign_base),
                'tax_amount': float(tax_amount_total * sign_base),
                'base_amount_currency': base_amount_total,
                'base_amount': base_amount_total,
                'name': 'Subtotal',
            }],
            'base_amount_currency': base_amount_total,
            'base_amount': base_amount_total,
            'tax_amount_currency': float(tax_amount_total * sign_base),
            'tax_amount': float(tax_amount_total * sign_base),
            'same_tax_base': False,
            'total_amount_currency': float(subtotal_xml + tax_amount_total),
            'total_amount': float(subtotal_xml + tax_amount_total),
            'display_in_company_currency': False,
        }

    def _find_payment_method(self, client: 'OdooClient', forma_pago: str) -> Optional[int]:
        """Busca el método de pago en l10n_mx_edi.payment.method por código (como ADD module)."""
        if not forma_pago:
            return False
        try:
            methods = client.search_read(
                'l10n_mx_edi.payment.method',
                [['code', '=', forma_pago]],
                fields=['id'],
                limit=1
            )
            if methods:
                return methods[0]['id']
        except OdooClientError as e:
            logger.warning(f"No se encontró l10n_mx_edi.payment.method para código {forma_pago}: {e}")
        return False

    def _get_sat_status(self, cfdi_data: CfdiParsedData) -> dict:
        """Consulta el estado del CFDI directamente en el SAT usando cfdiclient."""
        try:
            from cfdiclient import Validacion
            validador = Validacion()
            response = validador.obtener_estado(
                rfc_emisor=cfdi_data.rfc_emisor,
                rfc_receptor=cfdi_data.rfc_receptor,
                total=f"{cfdi_data.total:.2f}",
                uuid=cfdi_data.uuid
            )
            logger.info(f"Estado SAT obtenido para {cfdi_data.uuid}: {response}")
            return {
                'estado': response.get('Estado') or 'Vigente',
                'es_cancelable': response.get('EsCancelable'),
                'estado_cancelacion': response.get('EstatusCancelacion')
            }
        except Exception as e:
            logger.warning(f"No se pudo consultar estado SAT para {cfdi_data.uuid}: {e}")
            return {'estado': 'Vigente'}

    def _create_invoice(self, cfdi_data: CfdiParsedData, partner_id: int,
                        lines: list, move_type: str, company_id: int) -> int:
        """
        Método legacy - redirige a _create_invoice_base.

        Mantener para compatibilidad hacia atrás.
        """
        return self._create_invoice_base(cfdi_data, partner_id, lines, move_type, company_id)
    
    def get_xml_from_odoo_attachment(self, invoice_id: int) -> Optional[str]:
        """
        Obtiene el XML de un attachment de Odoo.
        
        Args:
            invoice_id: ID de la factura en Odoo
            
        Returns:
            Contenido XML decodificado o None
        """
        client = self._get_client()
        attachment = client.get_invoice_attachment(invoice_id, 'xml')
        
        if attachment and attachment.get('datas'):
            return base64.b64decode(attachment['datas']).decode('utf-8')
        
        return None


def sync_cfdi_to_odoo(empresa_id: int, cfdi_uuid: str, xml_content: str = None,
                       auto_post: bool = True) -> dict:
    """
    Función de conveniencia para sincronizar un CFDI.

    Args:
        empresa_id: ID de la empresa en aspeia_accounting
        cfdi_uuid: UUID del CFDI
        xml_content: Contenido XML opcional
        auto_post: Si True, publica la factura automáticamente (default: True)

    Returns:
        dict con resultado de la sincronización
    """
    try:
        connection = OdooConnection.objects.get(empresa_id=empresa_id, status='active')
    except OdooConnection.DoesNotExist:
        return {
            'status': 'error',
            'odoo_invoice_id': None,
            'message': f'No hay conexión Odoo activa para empresa ID={empresa_id}'
        }

    service = OdooInvoiceSyncService(connection)
    return service.sync_cfdi_to_odoo(cfdi_uuid, xml_content, auto_post)
