"""
Parser de CFDIs (Comprobantes Fiscales Digitales por Internet).

Soporta CFDI versiones 3.3 y 4.0 según el Anexo 20 del SAT.
Extrae datos del XML y los convierte en modelos Django.

Documentación: http://omawww.sat.gob.mx/tramitesyservicios/Paginas/anexo_20.htm
"""
import hashlib
import logging
import uuid as uuid_lib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional, Dict, Any, List

from lxml import etree

logger = logging.getLogger(__name__)


class CFDIParseError(Exception):
    """Error durante el parseo del CFDI."""
    pass


@dataclass
class CFDIParsedData:
    """Datos extraídos de un CFDI."""
    # Identificación
    uuid: str
    version: str
    serie: Optional[str]
    folio: Optional[str]

    # Emisor
    rfc_emisor: str
    nombre_emisor: Optional[str]
    regimen_fiscal_emisor: Optional[str]

    # Receptor
    rfc_receptor: str
    nombre_receptor: Optional[str]
    uso_cfdi: Optional[str]
    regimen_fiscal_receptor: Optional[str]
    domicilio_fiscal_receptor: Optional[str]

    # Comprobante
    tipo_comprobante: str  # I, E, P, T, N
    fecha_emision: datetime
    
    # Montos
    subtotal: Decimal
    total: Decimal
    descuento: Optional[Decimal]
    
    # Moneda
    moneda: str
    tipo_cambio: Optional[Decimal]
    
    # Pago
    forma_pago: Optional[str]
    metodo_pago: Optional[str]
    condiciones_pago: Optional[str]
    
    # Timbrado
    fecha_timbrado: Optional[datetime]
    no_certificado_sat: Optional[str]
    
    # Cancelación (si aplica)
    estado: str = 'Vigente'
    
    # Conceptos (opcional, para análisis detallado)
    conceptos: Optional[List[Dict[str, Any]]] = None
    
    # Impuestos resumen
    total_impuestos_trasladados: Optional[Decimal] = None
    total_impuestos_retenidos: Optional[Decimal] = None


class CFDIParser:
    """
    Parser de XMLs de CFDI 3.3 y 4.0.
    
    Extrae datos del XML usando lxml y los estructura para
    crear registros CfdiDocument.
    
    Ejemplo:
        parser = CFDIParser()
        data = parser.parse(xml_bytes)
        document = parser.to_model(data, empresa)
    """
    
    # Namespaces del SAT
    NS = {
        'cfdi': 'http://www.sat.gob.mx/cfd/4',
        'cfdi3': 'http://www.sat.gob.mx/cfd/3',
        'tfd': 'http://www.sat.gob.mx/TimbreFiscalDigital',
        'pago20': 'http://www.sat.gob.mx/Pagos20',
        'pago10': 'http://www.sat.gob.mx/Pagos',
        'nomina12': 'http://www.sat.gob.mx/nomina12',
    }
    
    def parse(self, xml_content: bytes) -> CFDIParsedData:
        """
        Parsea un XML de CFDI y extrae sus datos.
        
        Args:
            xml_content: Contenido del XML en bytes
            
        Returns:
            CFDIParsedData con todos los campos extraídos
            
        Raises:
            CFDIParseError: Si el XML es inválido o no es un CFDI válido
        """
        try:
            root = etree.fromstring(xml_content)
        except etree.XMLSyntaxError as e:
            raise CFDIParseError(f"XML inválido: {e}")
        
        # Detectar versión
        version = root.get('Version')
        if version == '4.0':
            ns_cfdi = 'cfdi'
        elif version == '3.3':
            ns_cfdi = 'cfdi3'
            # Ajustar namespace
            self.NS['cfdi'] = self.NS['cfdi3']
        else:
            raise CFDIParseError(f"Versión de CFDI no soportada: {version}")
        
        # Obtener nodos principales
        emisor = root.find(f'{{{self.NS[ns_cfdi]}}}Emisor')
        receptor = root.find(f'{{{self.NS[ns_cfdi]}}}Receptor')
        complemento = root.find(f'{{{self.NS[ns_cfdi]}}}Complemento')
        
        if emisor is None or receptor is None:
            raise CFDIParseError("CFDI incompleto: falta Emisor o Receptor")
        
        # Extraer UUID del TimbreFiscalDigital
        uuid = self._extract_uuid(complemento)
        if not uuid:
            raise CFDIParseError("CFDI sin UUID (TimbreFiscalDigital)")
        
        # Extraer datos del timbrado
        tfd = complemento.find(f'{{{self.NS["tfd"]}}}TimbreFiscalDigital') if complemento else None
        fecha_timbrado = None
        no_certificado_sat = None
        if tfd is not None:
            fecha_timbrado = self._parse_datetime(tfd.get('FechaTimbrado'))
            no_certificado_sat = tfd.get('NoCertificadoSAT')
        
        # Extraer impuestos
        impuestos = root.find(f'{{{self.NS[ns_cfdi]}}}Impuestos')
        total_trasladados = None
        total_retenidos = None
        if impuestos is not None:
            total_trasladados = self._to_decimal(impuestos.get('TotalImpuestosTrasladados'))
            total_retenidos = self._to_decimal(impuestos.get('TotalImpuestosRetenidos'))
        
        # Extraer conceptos (opcional)
        conceptos = self._extract_conceptos(root, ns_cfdi)
        
        return CFDIParsedData(
            # Identificación
            uuid=uuid,
            version=version,
            serie=root.get('Serie'),
            folio=root.get('Folio'),

            # Emisor
            rfc_emisor=emisor.get('Rfc', ''),
            nombre_emisor=emisor.get('Nombre'),
            regimen_fiscal_emisor=emisor.get('RegimenFiscal'),

            # Receptor
            rfc_receptor=receptor.get('Rfc', ''),
            nombre_receptor=receptor.get('Nombre'),
            uso_cfdi=receptor.get('UsoCFDI'),
            regimen_fiscal_receptor=receptor.get('RegimenFiscalReceptor'),
            domicilio_fiscal_receptor=receptor.get('DomicilioFiscalReceptor'),

            # Comprobante
            tipo_comprobante=root.get('TipoDeComprobante', 'I'),
            fecha_emision=self._parse_datetime(root.get('Fecha')),

            # Montos
            subtotal=self._to_decimal(root.get('SubTotal', '0')),
            total=self._to_decimal(root.get('Total', '0')),
            descuento=self._to_decimal(root.get('Descuento')),

            # Moneda
            moneda=root.get('Moneda', 'MXN'),
            tipo_cambio=self._to_decimal(root.get('TipoCambio')),

            # Pago
            forma_pago=root.get('FormaPago'),
            metodo_pago=root.get('MetodoPago'),
            condiciones_pago=root.get('CondicionesDePago'),

            # Timbrado
            fecha_timbrado=fecha_timbrado,
            no_certificado_sat=no_certificado_sat,

            # Conceptos
            conceptos=conceptos,

            # Impuestos
            total_impuestos_trasladados=total_trasladados,
            total_impuestos_retenidos=total_retenidos,
        )
    
    def _extract_uuid(self, complemento) -> Optional[str]:
        """Extrae el UUID del TimbreFiscalDigital."""
        if complemento is None:
            return None
        
        tfd = complemento.find(f'{{{self.NS["tfd"]}}}TimbreFiscalDigital')
        if tfd is not None:
            return tfd.get('UUID', '').upper()
        
        return None
    
    def _extract_conceptos(self, root, ns_cfdi: str) -> List[Dict[str, Any]]:
        """Extrae la lista de conceptos del CFDI."""
        conceptos = []
        conceptos_node = root.find(f'{{{self.NS[ns_cfdi]}}}Conceptos')
        
        if conceptos_node is None:
            return conceptos
        
        for concepto in conceptos_node.findall(f'{{{self.NS[ns_cfdi]}}}Concepto'):
            conceptos.append({
                'clave_prod_serv': concepto.get('ClaveProdServ'),
                'cantidad': self._to_decimal(concepto.get('Cantidad', '1')),
                'clave_unidad': concepto.get('ClaveUnidad'),
                'unidad': concepto.get('Unidad'),
                'descripcion': concepto.get('Descripcion', ''),
                'valor_unitario': self._to_decimal(concepto.get('ValorUnitario', '0')),
                'importe': self._to_decimal(concepto.get('Importe', '0')),
                'descuento': self._to_decimal(concepto.get('Descuento')),
                'objeto_imp': concepto.get('ObjetoImp'),
            })
        
        return conceptos
    
    def _parse_datetime(self, dt_str: Optional[str]) -> Optional[datetime]:
        """Parsea una fecha en formato ISO del SAT."""
        if not dt_str:
            return None
        try:
            # Formato SAT: 2024-01-15T10:30:00
            return datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
        except ValueError:
            logger.warning(f"Fecha inválida: {dt_str}")
            return None
    
    def _to_decimal(self, value: Optional[str]) -> Optional[Decimal]:
        """Convierte un string a Decimal de forma segura."""
        if value is None or value == '':
            return None
        try:
            return Decimal(value)
        except Exception:
            logger.warning(f"Valor decimal inválido: {value}")
            return None
    
    def to_model(self, data: CFDIParsedData, empresa, package=None, xml_content: bytes = None):
        """
        Convierte los datos parseados en un CfdiDocument.
        
        Args:
            data: Datos parseados del CFDI
            empresa: Instancia de Empresa (tenant)
            package: SatDownloadPackage de origen (opcional)
            xml_content: Contenido XML original para calcular hash
            
        Returns:
            CfdiDocument (no guardado en DB)
        """
        from apps.fiscal.models import CfdiDocument, UsoCfdi, FormaPago
        
        # Buscar catálogos si existen
        uso_cfdi_obj = None
        if data.uso_cfdi:
            uso_cfdi_obj = UsoCfdi.objects.filter(clave=data.uso_cfdi).first()
        
        forma_pago_obj = None
        if data.forma_pago:
            forma_pago_obj = FormaPago.objects.filter(clave=data.forma_pago).first()
        
        # Calcular hash si tenemos el XML
        xml_hash = None
        xml_size = None
        if xml_content:
            xml_hash = hashlib.sha256(xml_content).hexdigest()
            xml_size = len(xml_content)
        
        document = CfdiDocument(
            uuid=uuid_lib.UUID(data.uuid),
            company=empresa,
            rfc_emisor=data.rfc_emisor,
            rfc_receptor=data.rfc_receptor,
            tipo_cfdi=data.tipo_comprobante,
            uso_cfdi=uso_cfdi_obj,
            forma_pago=forma_pago_obj,
            metodo_pago=data.metodo_pago,
            total=data.total,
            moneda=data.moneda,
            fecha_emision=data.fecha_emision,
            source='SAT',
            download_package=package,
            estado_sat='Vigente',
            xml_hash=xml_hash,
            xml_size=xml_size,
            # Nuevos campos CFDI
            serie=data.serie,
            folio=data.folio,
            nombre_emisor=data.nombre_emisor,
            nombre_receptor=data.nombre_receptor,
            regimen_fiscal_emisor=data.regimen_fiscal_emisor,
            regimen_fiscal_receptor=data.regimen_fiscal_receptor,
            domicilio_fiscal_receptor=data.domicilio_fiscal_receptor,
            subtotal=data.subtotal or 0,
            descuento=data.descuento,
            fecha_timbrado=data.fecha_timbrado,
            no_certificado_sat=data.no_certificado_sat,
            cfdi_state='received',  # CFDIs del SAT vienen como 'recibidos'
        )

        return document
    
    def parse_and_save(self, xml_content: bytes, empresa, package=None, s3_path: str = None):
        """
        Parsea un XML, crea el CfdiDocument y lo guarda en la BD.
        
        Maneja duplicados: si ya existe un CFDI con el mismo UUID,
        no crea uno nuevo.
        
        Args:
            xml_content: Bytes del XML
            empresa: Empresa tenant
            package: Paquete SAT de origen
            s3_path: Ruta en S3 donde está guardado el XML
            
        Returns:
            tuple (CfdiDocument, created: bool)
        """
        from apps.fiscal.models import CfdiDocument
        
        data = self.parse(xml_content)
        
        # Verificar si ya existe
        existing = CfdiDocument.objects.filter(
            uuid=data.uuid,
            company=empresa
        ).first()
        
        if existing:
            logger.debug(f"CFDI {data.uuid} ya existe, omitiendo")
            return existing, False
        
        # Crear nuevo
        document = self.to_model(data, empresa, package, xml_content)
        document.s3_xml_path = s3_path
        document.save()
        
        logger.info(f"CFDI {data.uuid} guardado: {data.tipo_comprobante} ${data.total}")
        return document, True


# Instancia global para uso conveniente
parser = CFDIParser()
