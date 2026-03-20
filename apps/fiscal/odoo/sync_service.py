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
                traslados=traslados,
                retenciones=retenciones,
            )
            conceptos.append(line)
        
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
                # Ya existe, verificar estado
                result = self._handle_existing_invoice(existing_invoice, sync_log)
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

            # 4. Determinar tipo de factura
            move_type = self._get_move_type(cfdi_data, company_id)

            # 5. Buscar/crear partner
            partner_id = self._find_or_create_partner(cfdi_data, move_type, company_id)

            # 6. Crear la factura Date-Driven (sin UUID - será computado)
            invoice_id = self._create_invoice_base(
                cfdi_data, partner_id, move_type, company_id
            )

            # 8. Crear attachment con el XML
            xml_base64 = base64.b64encode(xml_content.encode('utf-8')).decode('utf-8')
            attachment_id = client.create_cfdi_attachment(
                invoice_id=invoice_id,
                xml_content_base64=xml_base64,
                uuid=cfdi_data.uuid,
                company_id=company_id
            )

            # 9. Crear l10n_mx_edi.document para que Odoo compute los campos
            cfdi_state = self._get_cfdi_state(move_type)
            cfdi_datetime = cfdi_data.fecha_timbrado.strftime('%Y-%m-%d %H:%M:%S')
            doc_id = client.create_l10n_mx_edi_document(
                invoice_id=invoice_id,
                attachment_id=attachment_id,
                state=cfdi_state,
                sat_state='not_defined',  # Estado SAT inicial
                cfdi_datetime=cfdi_datetime
            )

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

            # Actualizar last_sync en connection
            self.connection.last_sync = timezone.now()
            self.connection.save()

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
        # CFDIs recibidos usan 'invoice_received', emitidos usan 'invoice_sent'
        if move_type in ('in_invoice', 'in_refund'):
            return 'invoice_received'
        else:
            return 'invoice_sent'
    
    def _handle_existing_invoice(self, invoice: dict, sync_log: OdooSyncLog) -> dict:
        """
        Maneja el caso cuando la factura ya existe en Odoo.

        Verifica el estado actual y registra información útil para debugging.
        """
        # Obtener información de estados CFDI si están disponibles
        cfdi_uuid = invoice.get('l10n_mx_edi_cfdi_uuid', 'N/A')
        cfdi_state = invoice.get('l10n_mx_edi_cfdi_state', 'N/A')
        sat_state = invoice.get('l10n_mx_edi_cfdi_sat_state', 'N/A')

        sync_log.status = 'success'
        sync_log.odoo_invoice_id = invoice['id']
        sync_log.action_taken = 'verified'
        sync_log.response_data = {
            'invoice': invoice,
            'cfdi_uuid': cfdi_uuid,
            'cfdi_state': cfdi_state,
            'sat_state': sat_state,
        }
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
    
    # Constants for IEPS Whitelist
    IEPS_WHITELIST = {
        '50202200', '50202201', '50202202', '50202203', '50202204', '50202205', '50202206',
        '50202207', '50202208', '50202209', '50202210', '90101504', '90101502', '73131503',
        '50211500', '50211502', '50211503', '50211504', '50211506', '70141519', '15101505',
        '15101513', '15101514', '15101515', '50202303', '50202304', '50202306', '50202307',
        '50202308', '50202309', '50202311', '73131504', '50131700', '50131701', '50131702',
        '50131703', '50161800', '50161813', '50161814', '50161815', '50161500', '50161509',
        '50161510', '50161511', '50161512', '50192100', '50192109', '50192300', '50192301',
        '50192302', '50192303', '50192304', '50192400', '50192401', '50192403', '50192404',
        '50192405', '50192406', '50193100', '50193101', '50193102', '73131906', '73131903'
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
            product_id = self._find_or_create_product(concepto, company_id)
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
                    'quantity': float(concepto.cantidad) if not is_ieps else 1.0,
                    'price_unit': float(amount_val / concepto.cantidad) if not is_ieps and float(concepto.cantidad) > 0 else float(amount_val),
                    'balance': float(bal),
                    'debit': float(bal) if bal > 0 else 0.0,
                    'credit': float(-bal) if bal < 0 else 0.0,
                    'currency_id': currency['id'] if currency else None,
                    'amount_currency': float(amt_curr),
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
        
        # 2. Procesar Impuestos (Taxes Consolidados)
        tax_summary = {}
        for concepto in cfdi_data.conceptos:
            for tras in (concepto.traslados or []):
                key = f"TRAS_{tras['impuesto']}_{tras['tasa_o_cuota']}"
                if key not in tax_summary:
                    tax_summary[key] = {'type': 'traslado', 'impuesto': tras['impuesto'], 'tasa': tras['tasa_o_cuota'], 'importe': Decimal('0')}
                tax_summary[key]['importe'] += tras['importe']
            for ret in (concepto.retenciones or []):
                key = f"RET_{ret['impuesto']}_{ret['tasa_o_cuota']}"
                if key not in tax_summary:
                    tax_summary[key] = {'type': 'retencion', 'impuesto': ret['impuesto'], 'tasa': ret['tasa_o_cuota'], 'importe': Decimal('0')}
                tax_summary[key]['importe'] += ret['importe']
                
        for key, t_data in tax_summary.items():
            tasa_pct = float(t_data['tasa'] * 100) if t_data['type'] == 'traslado' else float(t_data['tasa'] * -100)
            if tasa_pct == 0:
                continue
                
            tax_id = self._find_or_create_tax(client, tasa_pct, tax_type, company_id, t_data['impuesto'], 'Tasa')
            tax_amount_xml = t_data['importe']
            if t_data['type'] == 'retencion':
                tax_amount_xml = -tax_amount_xml
                
            tax_amount_company = self._quantize((tax_amount_xml * tipo_cambio) if has_foreign_currency else tax_amount_xml)
            tax_balance = tax_amount_company * sign_base
            tax_amount_currency = self._quantize(tax_amount_xml * sign_base if has_foreign_currency else tax_balance)
            
            tax_account_id = None
            tax_repartition_line_id = False
            if tax_id:
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
                'name': f"Impuesto {t_data['impuesto']} {tasa_pct}%",
                'account_id': tax_account_id,
                'balance': float(tax_balance),
                'debit': float(tax_balance) if tax_balance > 0 else 0.0,
                'credit': float(-tax_balance) if tax_balance < 0 else 0.0,
                'currency_id': currency['id'] if currency else None,
                'amount_currency': float(tax_amount_currency),
                'display_type': 'tax',
                'tax_line_id': tax_id,
                'tax_repartition_line_id': tax_repartition_line_id,
                'partner_id': partner_id,
            }
            move_lines.append((0, 0, tax_line))

        # 3. Línea Contrapartida (Receivable/Payable)
        net_total_xml = cfdi_data.total
        payable_account_id = client.get_partner_account(partner_id, is_supplier)
        if not payable_account_id:
            logger.warning(f"Partner {partner_id} sin cuenta CxC/CxP, usando diario por defecto.")
            payable_account_id = journal_id
            
        counterpart_company = self._quantize((net_total_xml * tipo_cambio) if has_foreign_currency else net_total_xml)
        counterpart_balance = -counterpart_company * sign_base
        counterpart_amount_currency = self._quantize(-net_total_xml * sign_base if has_foreign_currency else counterpart_balance)
        
        counterpart_line = {
            'account_id': payable_account_id,
            'balance': float(counterpart_balance),
            'debit': float(counterpart_balance) if counterpart_balance > 0 else 0.0,
            'credit': float(-counterpart_balance) if counterpart_balance < 0 else 0.0,
            'currency_id': currency['id'] if currency else None,
            'amount_currency': float(counterpart_amount_currency),
            'partner_id': partner_id,
            'display_type': 'payment_term',
        }
        
        # 4. Reconciliar Centavos Sueltos
        sum_debit = sum(l[2]['debit'] for l in move_lines)
        sum_credit = sum(l[2]['credit'] for l in move_lines)
        diff = sum_debit - sum_credit
        if round(abs(diff), 2) > 0.00:
            logger.info(f"Ajustando {diff} internamente en el CFDI para cuadrar débito/crédito.")
            if diff > 0:
                counterpart_line['credit'] += float(diff)
                counterpart_line['balance'] -= float(diff)
            else:
                counterpart_line['debit'] += float(abs(diff))
                counterpart_line['balance'] += float(abs(diff))

        move_lines.append((0, 0, counterpart_line))

        ref = f"{cfdi_data.serie}{cfdi_data.folio}" if cfdi_data.serie else (cfdi_data.folio or '')

        invoice_vals = {
            'move_type': move_type,
            'partner_id': partner_id,
            'company_id': company_id,
            'invoice_date': cfdi_data.fecha.strftime('%Y-%m-%d'),
            'ref': ref,
            'line_ids': move_lines,
        }

        if currency:
            invoice_vals['currency_id'] = currency['id']

        if cfdi_data.forma_pago:
            invoice_vals['narration'] = f"Forma de pago: {cfdi_data.forma_pago}"
        if cfdi_data.metodo_pago:
            narration = invoice_vals.get('narration', '')
            invoice_vals['narration'] = f"{narration}\nMétodo de pago: {cfdi_data.metodo_pago}".strip()

        # Usar context para ignorar check_move_validity de odoo localmente 
        # y así respetar los montos manuales calculados por el XML
        invoice_id = client.create('account.move', invoice_vals, context={'check_move_validity': False})
        logger.info(f"Factura Data-Driven creada en Odoo: ID={invoice_id}, ref={ref}")

        return invoice_id

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
