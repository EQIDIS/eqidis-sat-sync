"""
Firmador de XML para autenticación con FIEL ante el SAT.

El SAT requiere que los requests SOAP estén firmados digitalmente
usando el estándar XML-DSIG con el certificado FIEL del contribuyente.
"""
import base64
import hashlib
from datetime import datetime, timedelta
from io import BytesIO

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from django.core.files.storage import default_storage
from lxml import etree

from apps.core.encryption import ModelEncryption


class XMLSigner:
    """
    Firma documentos XML con certificado FIEL para autenticación SAT.
    
    El proceso de firma sigue el estándar XML Signature (XML-DSIG),
    específicamente el perfil requerido por los Web Services del SAT.
    """
    
    # Namespaces usados por el SAT
    NS = {
        'ds': 'http://www.w3.org/2000/09/xmldsig#',
        's': 'http://schemas.xmlsoap.org/soap/envelope/',
        'u': 'http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd',
        'o': 'http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd',
    }
    
    def __init__(self, certificate):
        """
        Inicializa el firmador con un certificado FIEL.
        
        Args:
            certificate: Instancia de CfdiCertificate con tipo='FIEL'
        """
        self.certificate = certificate
        self._private_key = None
        self._x509_cert = None
        self._load_credentials()
    
    def _load_credentials(self):
        """Carga el certificado y llave privada desde S3."""
        # Cargar certificado .cer
        cer_content = self._read_s3_file(self.certificate.s3_cer_path)
        self._x509_cert = x509.load_der_x509_certificate(cer_content)
        
        # Cargar llave privada .key
        key_content = self._read_s3_file(self.certificate.s3_key_path)
        
        # Desencriptar contraseña
        password = self.certificate.password
        if not password:
            raise ValueError("No se pudo obtener la contraseña del certificado")
        
        # El archivo .key del SAT está en formato PKCS#8 DER encriptado
        self._private_key = serialization.load_der_private_key(
            key_content,
            password=password.encode('utf-8')
        )
    
    def _read_s3_file(self, path):
        """Lee un archivo desde S3 y retorna su contenido como bytes."""
        with default_storage.open(path, 'rb') as f:
            return f.read()
    
    @property
    def certificate_b64(self):
        """Certificado en formato Base64 (para incluir en el XML)."""
        return base64.b64encode(
            self._x509_cert.public_bytes(serialization.Encoding.DER)
        ).decode('utf-8')
    
    @property
    def serial_number(self):
        """Número de serie del certificado."""
        return format(self._x509_cert.serial_number, 'x').upper()
    
    def sign_soap_request(self, soap_body_content, action_namespace):
        """
        Crea y firma un request SOAP completo para el SAT.
        
        Args:
            soap_body_content: Contenido XML del Body del SOAP
            action_namespace: Namespace de la acción SOAP
            
        Returns:
            str: SOAP Envelope firmado como string XML
        """
        # Crear estructura SOAP
        timestamp_id = f"_0"
        body_id = "_1"
        
        # Timestamps (requeridos por WS-Security)
        now = datetime.utcnow()
        created = now.strftime('%Y-%m-%dT%H:%M:%S.000Z')
        expires = (now + timedelta(minutes=5)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
        
        # Construir el envelope
        envelope = self._build_envelope(
            soap_body_content, 
            timestamp_id, 
            body_id,
            created,
            expires
        )
        
        # Calcular digests
        timestamp_digest = self._calculate_digest(
            envelope.find('.//u:Timestamp', self.NS)
        )
        body_digest = self._calculate_digest(
            envelope.find('.//s:Body', self.NS)
        )
        
        # Crear SignedInfo
        signed_info = self._create_signed_info(
            timestamp_id, timestamp_digest,
            body_id, body_digest
        )
        
        # Firmar
        signature_value = self._sign(signed_info)
        
        # Insertar firma en el envelope
        self._insert_signature(envelope, signed_info, signature_value)
        
        return etree.tostring(envelope, encoding='unicode')
    
    def _build_envelope(self, body_content, timestamp_id, body_id, created, expires):
        """Construye el SOAP Envelope base."""
        envelope_str = f'''<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"
            xmlns:u="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">
    <s:Header>
        <o:Security xmlns:o="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
                    s:mustUnderstand="1">
            <u:Timestamp u:Id="{timestamp_id}">
                <u:Created>{created}</u:Created>
                <u:Expires>{expires}</u:Expires>
            </u:Timestamp>
        </o:Security>
    </s:Header>
    <s:Body u:Id="{body_id}">
        {body_content}
    </s:Body>
</s:Envelope>'''
        return etree.fromstring(envelope_str.encode('utf-8'))
    
    def _calculate_digest(self, element):
        """Calcula el digest SHA-256 de un elemento usando C14N."""
        # Canonicalizar el elemento
        c14n = etree.tostring(element, method='c14n', exclusive=True, with_comments=False)
        # Calcular SHA-256
        digest = hashlib.sha256(c14n).digest()
        return base64.b64encode(digest).decode('utf-8')
    
    def _create_signed_info(self, timestamp_id, timestamp_digest, body_id, body_digest):
        """Crea el elemento SignedInfo con las referencias."""
        signed_info_str = f'''<ds:SignedInfo xmlns:ds="http://www.w3.org/2000/09/xmldsig#">
    <ds:CanonicalizationMethod Algorithm="http://www.w3.org/2001/10/xml-exc-c14n#"/>
    <ds:SignatureMethod Algorithm="http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"/>
    <ds:Reference URI="#{timestamp_id}">
        <ds:Transforms>
            <ds:Transform Algorithm="http://www.w3.org/2001/10/xml-exc-c14n#"/>
        </ds:Transforms>
        <ds:DigestMethod Algorithm="http://www.w3.org/2001/04/xmlenc#sha256"/>
        <ds:DigestValue>{timestamp_digest}</ds:DigestValue>
    </ds:Reference>
    <ds:Reference URI="#{body_id}">
        <ds:Transforms>
            <ds:Transform Algorithm="http://www.w3.org/2001/10/xml-exc-c14n#"/>
        </ds:Transforms>
        <ds:DigestMethod Algorithm="http://www.w3.org/2001/04/xmlenc#sha256"/>
        <ds:DigestValue>{body_digest}</ds:DigestValue>
    </ds:Reference>
</ds:SignedInfo>'''
        return etree.fromstring(signed_info_str.encode('utf-8'))
    
    def _sign(self, signed_info):
        """Firma el SignedInfo con la llave privada RSA-SHA256."""
        # Canonicalizar SignedInfo
        c14n = etree.tostring(signed_info, method='c14n', exclusive=True, with_comments=False)
        
        # Firmar con RSA-SHA256
        signature = self._private_key.sign(
            c14n,
            padding.PKCS1v15(),
            hashes.SHA256()
        )
        
        return base64.b64encode(signature).decode('utf-8')
    
    def _insert_signature(self, envelope, signed_info, signature_value):
        """Inserta el bloque Signature completo en el Security header."""
        security = envelope.find('.//o:Security', self.NS)
        
        # Crear elemento Signature
        signature_str = f'''<ds:Signature xmlns:ds="http://www.w3.org/2000/09/xmldsig#">
    {etree.tostring(signed_info, encoding='unicode')}
    <ds:SignatureValue>{signature_value}</ds:SignatureValue>
    <ds:KeyInfo>
        <o:SecurityTokenReference xmlns:o="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">
            <o:X509Data>
                <o:X509IssuerSerial>
                    <o:X509IssuerName>{self._x509_cert.issuer.rfc4514_string()}</o:X509IssuerName>
                    <o:X509SerialNumber>{self._x509_cert.serial_number}</o:X509SerialNumber>
                </o:X509IssuerSerial>
            </o:X509Data>
        </o:SecurityTokenReference>
    </ds:KeyInfo>
</ds:Signature>'''
        
        signature_elem = etree.fromstring(signature_str.encode('utf-8'))
        security.append(signature_elem)
