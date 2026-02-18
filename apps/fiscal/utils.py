import base64
from datetime import datetime
from django.core.exceptions import ValidationError
from django.utils import timezone
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization

def validate_certificate_key_pair(cer_file, key_file, password):
    """
    Valida que el archivo .cer y .key correspondan y que la contraseña sea correcta.
    Retorna los datos del certificado si es válido.
    
    Args:
        cer_file: Archivo .cer (InMemoryUploadedFile o bytes)
        key_file: Archivo .key (InMemoryUploadedFile o bytes)
        password: Contraseña de la llave privada (str)
        
    Returns:
        dict: Datos extraídos del certificado (serial, rfc, vigencia) või lanza ValidationError
    """
    try:
        # 1. Leer archivos
        cer_data = cer_file.read() if hasattr(cer_file, 'read') else cer_file
        key_data = key_file.read() if hasattr(key_file, 'read') else key_file
        
        # Reset pointers if files
        if hasattr(cer_file, 'seek'): cer_file.seek(0)
        if hasattr(key_file, 'seek'): key_file.seek(0)
        
        # 2. Cargar Certificado X.509
        cert = x509.load_der_x509_certificate(cer_data, default_backend())
        
        # 3. Validar Contraseña y Cargar Llave Privada
        private_key = serialization.load_der_private_key(
            key_data,
            password=password.encode('utf-8'),
            backend=default_backend()
        )
        
        # 4. Validar Correspondencia (Llave Pública)
        public_key_cert = cert.public_key()
        public_key_key = private_key.public_key()
        
        # Serializar ambas a PEM para comparar
        pem_cert = public_key_cert.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        pem_key = public_key_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        
        if pem_cert != pem_key:
            raise ValidationError("El archivo .cer no corresponde al archivo .key proporcionado.")
            
        # 5. Extraer Datos del Certificado
        serial_number = '{0:x}'.format(cert.serial_number)
        # Formato SAT a veces requiere padding o ajuste, este es el raw hex
        # Para SAT suele ser decimal string, verifiquemos estándar
        # SAT usa decimal string representation para el No. de Serie
        serial_number_dec = str(cert.serial_number)
        
        # Extraer RFC del Subject (OID 2.5.4.45 o x500UniqueIdentifier para SAT)
        # El SAT mete el RFC en el Subject. A veces como CN o OID específico.
        # Simplificación: Extraer del CN si tiene formato RFC, o buscar el OID.
        subject = cert.subject
        rfc = None
        # OID común para RFC en SAT: 2.5.4.45 (x500UniqueIdentifier)
        oid_rfc = x509.ObjectIdentifier("2.5.4.45")
        
        try:
            rfc_attr = subject.get_attributes_for_oid(oid_rfc)
            if rfc_attr:
                rfc = rfc_attr[0].value
                # A veces viene con basura o prefijos, limpiar
                rfc = rfc.strip().split(' ')[0] # Simple heuristic
        except Exception:
            pass
            
        if not rfc:
             # Fallback: Buscar en CommonName (CN)
             try:
                cn = subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value
                # El CN del SAT suele ser "NOMBRE RAZON SOCIAL" o similar, no siempre el RFC
                # Pero en FIEL a veces está.
                pass
             except:
                pass
        
        # 6. Validar Vigencia
        now = datetime.utcnow()
        if now < cert.not_valid_before or now > cert.not_valid_after:
            raise ValidationError(f"El certificado no está vigente. Venció el {cert.not_valid_after}")
            
        # Detección de tipo con cryptography
        tipo_certificado_main = 'CSD'
        try:
            ku = cert.extensions.get_extension_for_oid(x509.ExtensionOID.KEY_USAGE).value
            if ku.key_encipherment or ku.data_encipherment:
                tipo_certificado_main = 'FIEL'
        except x509.ExtensionNotFound:
            pass

        return {
            'serial_number': serial_number_dec,
            'rfc': rfc,
            'valid_from': cert.not_valid_before,
            'valid_to': cert.not_valid_after,
            'subject': subject.rfc4514_string(),
            'tipo': tipo_certificado_main
        }

    except ValueError as e:
        # Error específico de cryptography cuando el Subject tiene caracteres raros (ASN.1 InvalidValue)
        # Intentamos fallback con OpenSSL (pyopenssl) que es más permisivo
        if "subject" in str(e) or "ParseError" in str(e):
            try:
                import OpenSSL
                
                # Reset pointer
                if hasattr(cer_file, 'seek'): cer_file.seek(0)
                
                # Cargar con OpenSSL
                x509_obj = OpenSSL.crypto.load_certificate(
                    OpenSSL.crypto.FILETYPE_ASN1, 
                    cer_data
                )
                
                # Validar Fechas
                not_after_bytes = x509_obj.get_notAfter()
                not_before_bytes = x509_obj.get_notBefore()
                
                # OpenSSL retorna bytes: b'20270114000000Z' - YYYYMMDDHHMMSSZ
                not_after_str = not_after_bytes.decode('utf-8')
                not_before_str = not_before_bytes.decode('utf-8')
                
                expiry_date = datetime.strptime(not_after_str[:14], '%Y%m%d%H%M%S')
                start_date = datetime.strptime(not_before_str[:14], '%Y%m%d%H%M%S')
                
                if datetime.utcnow() > expiry_date:
                    raise ValidationError(f"El certificado expiró el {expiry_date}")

                # Validar Key Pair
                # ESTRATEGIA HÍBRIDA:
                # El certificado lo leemos con OpenSSL (porque soporta Subject "sucios").
                # La llave privada la leemos con Cryptography (porque soporta DER cifrado mejor que pyOpenSSL).
                try:
                    private_key = serialization.load_der_private_key(
                        key_data,
                        password=password.encode('utf-8'),
                        backend=default_backend()
                    )
                    
                    # 1. Obtener PubKey del Certificado (OpenSSL)
                    pub_openssl = x509_obj.get_pubkey()
                    pub_openssl_pem = OpenSSL.crypto.dump_publickey(OpenSSL.crypto.FILETYPE_PEM, pub_openssl)
                    
                    # 2. Obtener PubKey de la Llave Privada (Cryptography)
                    pub_crypto = private_key.public_key()
                    pub_crypto_pem = pub_crypto.public_bytes(
                        encoding=serialization.Encoding.PEM,
                        format=serialization.PublicFormat.SubjectPublicKeyInfo
                    )
                    
                    # 3. Comparar (eliminando espacios/saltos por seguridad)
                    if pub_openssl_pem.strip() != pub_crypto_pem.strip():
                        raise ValidationError("El archivo .cer no corresponde al archivo .key proporcionado.")
                        
                except Exception as e:
                    if "Bad decrypt" in str(e) or "mac check failed" in str(e):
                        raise ValidationError("La contraseña de la llave privada es incorrecta.")
                    raise ValidationError(f"Error al validar correspondencia Key-Cer: {str(e)}")

                # Extraer RFC del Subject
                # OpenSSL subject es un X509Name
                subj = x509_obj.get_subject()
                
                # Buscar OID 2.5.4.45 (x500UniqueIdentifier)
                # En pyopenssl suele usarse get_components()
                # Retorna lista de tuplas (b'OID', b'VALUE')
                rfc_fallback = None
                for name, value in subj.get_components():
                    if name == b'x500UniqueIdentifier' or name == b'2.5.4.45':
                        rfc_fallback = value.decode('utf-8', errors='ignore')
                        break
                
                if not rfc_fallback:
                     # Intentar CN
                     rfc_fallback = subj.commonName
                
                if rfc_fallback:
                    rfc_fallback = rfc_fallback.strip().split(' ')[0]

                # Detección automática de Tipo (FIEL vs CSD)
                # Lógica: FIEL permite KeyEncipherment/DataEncipherment. CSD solo DigitalSignature/NonRepudiation.
                tipo_certificado = 'CSD' # Default
                
                # Intentar leer extensiones con OpenSSL
                try:
                    for i in range(x509_obj.get_extension_count()):
                        ext = x509_obj.get_extension(i)
                        short_name = ext.get_short_name() # b'keyUsage'
                        if short_name == b'keyUsage':
                            # El valor suele ser string legible en OpenSSL "Digital Signature, Non Repudiation, ..."
                            usage_str = str(ext)
                            if "Key Encipherment" in usage_str or "Data Encipherment" in usage_str:
                                tipo_certificado = 'FIEL'
                except Exception:
                    pass # Keep default
                
                return {
                    'serial_number': str(x509_obj.get_serial_number()),
                    'rfc': rfc_fallback,
                    'valid_from': start_date,
                    'valid_to': expiry_date,
                    'subject': 'parsed_via_openssl_fallback',
                    'tipo': tipo_certificado
                }

            except ImportError:
                 raise ValidationError("Error crítico: Certificado malformado y librería OpenSSL no disponible.")
            except Exception as os_e:
                # Si fallan ambos
                if "Bad decrypt" in str(os_e) or "bad decrypt" in str(os_e):
                     raise ValidationError("La contraseña de la llave privada es incorrecta.")
                raise ValidationError(f"Error al procesar certificado (crypto+openssl): {str(e)} -> {str(os_e)}")

        # Error común de contraseña
        if "Bad decrypt" in str(e) or "mac check failed" in str(e):
            raise ValidationError("La contraseña de la llave privada es incorrecta.")
        raise ValidationError(f"Error al procesar archivos de certificado: {str(e)}")
    

    except Exception as e:
        raise ValidationError(f"Error inesperado al validar certificado: {str(e)}")
