"""
Cliente para Web Services de Descarga Masiva del SAT.

Usa la librería satcfdi para manejar la comunicación con el SAT.
https://satcfdi.readthedocs.io/en/stable/pages/getting_started/45_cfdi_descarga_massiva.html

Implementa el flujo de 3 pasos:
1. SolicitaDescarga - Crea una solicitud de descarga
2. VerificaSolicitudDescarga - Verifica el estado de la solicitud
3. Descarga - Descarga los paquetes ZIP
"""
import base64
import logging
from datetime import datetime, date
from typing import Optional, Dict, Any, List

from django.core.files.storage import default_storage

from apps.fiscal.models import CfdiCertificate

logger = logging.getLogger(__name__)


class SATClientError(Exception):
    """Error base para el cliente SAT."""
    pass


class SATAuthError(SATClientError):
    """Error de autenticación con el SAT."""
    pass


class SATRequestError(SATClientError):
    """Error en la solicitud al SAT."""
    pass


class SATClient:
    """
    Cliente para Web Services de Descarga Masiva del SAT.
    
    Requiere un certificado FIEL activo para autenticación.
    Usa satcfdi para manejar el protocolo SOAP.
    
    Ejemplo:
        fiel = CfdiCertificate.objects.get(tipo='FIEL', status='active')
        client = SATClient(fiel)
        
        # Paso 1: Solicitar descarga
        result = client.solicitar_descarga(
            fecha_inicio='2024-01-01',
            fecha_fin='2024-01-31',
            tipo='recibidos'
        )
        
        # Paso 2: Verificar estado
        status = client.verificar_solicitud(result['id_solicitud'])
        
        # Paso 3: Descargar paquetes
        if status['estado'] == 'Terminada':
            for pkg_id in status['paquetes']:
                content = client.descargar_paquete(pkg_id)
    """
    
    def __init__(self, certificate: CfdiCertificate):
        """
        Inicializa el cliente con un certificado FIEL.
        
        Args:
            certificate: CfdiCertificate con tipo='FIEL' y status='active'
            
        Raises:
            ValueError: Si el certificado no es FIEL o no está activo
        """
        if certificate.tipo != 'FIEL':
            raise ValueError(f"Se requiere certificado FIEL, se recibió {certificate.tipo}")
        
        if certificate.status != 'active':
            raise ValueError(f"Certificado no activo: {certificate.status}")
        
        self.certificate = certificate
        self.rfc = certificate.rfc
        self._signer = None
        self._sat_service = None
        self._load_signer()
    
    def _load_signer(self):
        """Carga el Signer y servicio SAT usando satcfdi."""
        from satcfdi.models import Signer
        from satcfdi.pacs.sat import SAT
        
        # Descargar archivos de S3
        cer_content = self._read_s3_file(self.certificate.s3_cer_path)
        key_content = self._read_s3_file(self.certificate.s3_key_path)
        
        # Obtener contraseña desencriptada
        if not (self.certificate.encrypted_password or self.certificate.encrypted_password.strip()):
            raise SATAuthError(
                "La contraseña del certificado no está guardada. "
                "Suba de nuevo la FIEL desde la app (Fiscal → certificados) e ingrese la contraseña al subirla."
            )
        password = self.certificate.password
        if not password:
            raise SATAuthError(
                "No se pudo desencriptar la contraseña del certificado. "
                "Asegúrese de que DJANGO_SECRET_KEY sea idéntica en la aplicación web y en el worker de Celery (mismo .env)."
            )
        
        # Crear Signer de satcfdi
        self._signer = Signer.load(
            certificate=cer_content,
            key=key_content,
            password=password
        )
        
        # Crear servicio SAT
        self._sat_service = SAT(signer=self._signer)
        
        logger.info(f"SAT Service inicializado para RFC: {self._signer.rfc}")
    
    def _read_s3_file(self, path: str) -> bytes:
        """Lee un archivo desde S3 y retorna su contenido como bytes."""
        with default_storage.open(path, 'rb') as f:
            return f.read()
    
    def _parse_date(self, date_str: str) -> date:
        """Convierte string YYYY-MM-DD a date."""
        return datetime.strptime(date_str, '%Y-%m-%d').date()
    
    def solicitar_descarga(
        self,
        fecha_inicio: str,
        fecha_fin: str,
        tipo: str = 'recibidos',
        rfc_receptor: Optional[str] = None,
        rfc_emisor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Solicita una descarga masiva de CFDIs al SAT.
        
        Args:
            fecha_inicio: Fecha inicio YYYY-MM-DD
            fecha_fin: Fecha fin YYYY-MM-DD
            tipo: 'emitidos' o 'recibidos'
            rfc_receptor: RFC del receptor (para recibidos)
            rfc_emisor: RFC del emisor (para emitidos)
            
        Returns:
            dict con 'id_solicitud', 'cod_estatus', 'mensaje'
        """
        from satcfdi.pacs.sat import TipoDescargaMasivaTerceros, EstadoComprobante
        
        fecha_inicial = self._parse_date(fecha_inicio)
        fecha_final = self._parse_date(fecha_fin)
        
        logger.info(f"Solicitando descarga {tipo}: {fecha_inicio} - {fecha_fin}")
        
        try:
            if tipo == 'emitidos':
                response = self._sat_service.recover_comprobante_emitted_request(
                    fecha_inicial=fecha_inicial,
                    fecha_final=fecha_final,
                    rfc_emisor=rfc_emisor or self._signer.rfc,
                    tipo_solicitud=TipoDescargaMasivaTerceros.CFDI,
                )
            else:  # recibidos
                response = self._sat_service.recover_comprobante_received_request(
                    fecha_inicial=fecha_inicial,
                    fecha_final=fecha_final,
                    rfc_receptor=rfc_receptor or self._signer.rfc,
                    tipo_solicitud=TipoDescargaMasivaTerceros.CFDI,
                    estado_comprobante=EstadoComprobante.VIGENTE,
                )
            
            logger.info(f"Solicitud enviada: {response}")
            
            return {
                'id_solicitud': response.get('IdSolicitud'),
                'cod_estatus': response.get('CodEstatus'),
                'mensaje': response.get('Mensaje'),
            }
            
        except Exception as e:
            logger.error(f"Error solicitando descarga: {e}")
            raise SATRequestError(f"Error solicitando descarga: {e}")
    
    def verificar_solicitud(self, id_solicitud: str) -> Dict[str, Any]:
        """
        Verifica el estado de una solicitud de descarga.
        
        Args:
            id_solicitud: ID de solicitud retornado por solicitar_descarga
            
        Returns:
            dict con 'estado', 'cod_estatus', 'mensaje', 'paquetes'
        """
        from satcfdi.pacs.sat import EstadoSolicitud
        
        logger.info(f"Verificando solicitud: {id_solicitud}")
        
        try:
            response = self._sat_service.recover_comprobante_status(id_solicitud)
            
            logger.info(f"Resultado verificación: {response}")
            
            # Extraer lista de paquetes
            paquetes = response.get('IdsPaquetes', [])
            if isinstance(paquetes, str):
                paquetes = [paquetes] if paquetes else []
            
            # Obtener estado
            estado = response.get('EstadoSolicitud')
            if hasattr(estado, 'value'):
                estado = estado.value
            elif hasattr(estado, 'name'):
                estado = estado.name
            
            return {
                'estado': str(estado) if estado else None,
                'cod_estatus': response.get('CodEstatus'),
                'mensaje': response.get('Mensaje'),
                'numero_cfdis': response.get('NumeroCFDIs'),
                'paquetes': paquetes,
            }
            
        except Exception as e:
            logger.error(f"Error verificando solicitud: {e}")
            raise SATRequestError(f"Error verificando solicitud: {e}")
    
    def descargar_paquete(self, id_paquete: str) -> bytes:
        """
        Descarga un paquete ZIP con CFDIs.
        
        Args:
            id_paquete: ID del paquete retornado por verificar_solicitud
            
        Returns:
            bytes: Contenido del ZIP
        """
        logger.info(f"Descargando paquete: {id_paquete}")
        
        try:
            response, paquete = self._sat_service.recover_comprobante_download(
                id_paquete=id_paquete
            )
            
            # El paquete viene en base64
            if isinstance(paquete, str):
                paquete = base64.b64decode(paquete)
            
            logger.info(f"Paquete descargado: {len(paquete)} bytes")
            return paquete
            
        except Exception as e:
            logger.error(f"Error descargando paquete: {e}")
            raise SATRequestError(f"Error descargando paquete: {e}")
    
    def validar_estado_cfdi(
        self,
        rfc_emisor: str,
        rfc_receptor: str,
        total: str,
        uuid: str,
    ) -> Dict[str, Any]:
        """
        Valida el estado actual de un CFDI individual ante el SAT.
        
        Usa cfdiclient.Validacion que consulta directamente al SAT
        sin necesidad del XML original.
        
        Args:
            rfc_emisor: RFC del emisor
            rfc_receptor: RFC del receptor  
            total: Total del CFDI (string con 2 decimales, ej: "1234.56")
            uuid: UUID del CFDI
            
        Returns:
            dict con 'estado', 'es_cancelable', 'estado_cancelacion', 'response_raw'
        """
        from cfdiclient import Validacion
        
        logger.info(f"Validando estado CFDI: {uuid}")
        
        try:
            validador = Validacion()
            response = validador.obtener_estado(
                rfc_emisor=rfc_emisor,
                rfc_receptor=rfc_receptor,
                total=total,
                uuid=uuid,
            )
            
            logger.info(f"Estado CFDI {uuid}: {response}")
            
            # cfdiclient devuelve dict con Estado, EsCancelable, EstatusCancelacion
            estado = response.get('Estado') or response.get('estado')
            es_cancelable = response.get('EsCancelable') or response.get('es_cancelable')
            estado_cancelacion = response.get('EstatusCancelacion') or response.get('estado_cancelacion')
            
            return {
                'estado': estado,
                'es_cancelable': es_cancelable,
                'estado_cancelacion': estado_cancelacion,
                'response_raw': str(response),
            }
            
        except Exception as e:
            logger.error(f"Error validando CFDI {uuid}: {e}")
            raise SATRequestError(f"Error validando CFDI: {e}")
    
    def verificar_lista_69b(self, rfc: str) -> Dict[str, Any]:
        """
        Consulta si un RFC está en la Lista Negra 69-B (EFOS).
        
        Los contribuyentes en esta lista emiten facturas por operaciones
        simuladas (factureras). Es crítico validar proveedores antes de
        deducir sus facturas.
        
        Args:
            rfc: RFC a consultar
            
        Returns:
            dict con 'is_efos', 'tipo', 'fecha_publicacion', 'response_raw'
            - is_efos: True si está en lista negra
            - tipo: 'definitivo', 'presunto', 'desvirtuado', 'sentencia_favorable'
        """
        logger.info(f"Consultando Lista 69-B para RFC: {rfc}")
        
        try:
            response = self._sat_service.list_69b(rfc)
            
            logger.info(f"Resultado 69-B {rfc}: {response}")
            
            if response is None:
                # No está en la lista
                return {
                    'is_efos': False,
                    'tipo': None,
                    'fecha_publicacion': None,
                    'response_raw': 'No encontrado en lista 69-B',
                }
            
            # TaxpayerStatus tiene atributos como .definitivo, .presunto, .desvirtuado
            is_efos = True
            tipo = None
            fecha = None
            
            if hasattr(response, 'definitivo') and response.definitivo:
                tipo = 'definitivo'
                fecha = response.definitivo
            elif hasattr(response, 'presunto') and response.presunto:
                tipo = 'presunto'
                fecha = response.presunto
            elif hasattr(response, 'desvirtuado') and response.desvirtuado:
                tipo = 'desvirtuado'
                fecha = response.desvirtuado
                is_efos = False  # Desvirtuado ya no es EFOS
            elif hasattr(response, 'sentencia_favorable') and response.sentencia_favorable:
                tipo = 'sentencia_favorable'
                fecha = response.sentencia_favorable
                is_efos = False  # Sentencia favorable ya no es EFOS
            
            return {
                'is_efos': is_efos,
                'tipo': tipo,
                'fecha_publicacion': fecha,
                'response_raw': str(response),
            }
            
        except Exception as e:
            logger.error(f"Error consultando Lista 69-B para {rfc}: {e}")
            raise SATRequestError(f"Error consultando Lista 69-B: {e}")
    
    def obtener_cancelaciones_pendientes(self) -> List[str]:
        """
        Obtiene la lista de UUIDs de facturas con cancelación pendiente.
        
        Estas son facturas que alguien intentó cancelar y requieren
        aceptación o rechazo del receptor.
        
        Returns:
            list[str]: Lista de UUIDs pendientes de aceptar/rechazar
        """
        logger.info(f"Obteniendo cancelaciones pendientes para RFC: {self.rfc}")
        
        try:
            response = self._sat_service.pending(self.rfc)
            
            logger.info(f"Cancelaciones pendientes: {len(response)} UUIDs")
            
            return response if response else []
            
        except Exception as e:
            logger.error(f"Error obteniendo cancelaciones pendientes: {e}")
            raise SATRequestError(f"Error obteniendo cancelaciones pendientes: {e}")

