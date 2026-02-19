import base64
import logging
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from django.conf import settings
from django.utils.encoding import force_bytes, force_str

logger = logging.getLogger(__name__)


class ModelEncryption:
    """
    Utilidad para encriptar campos sensibles en base de datos
    usando el SECRET_KEY de Django como semilla.
    """
    _fernet = None

    @classmethod
    def get_fernet(cls):
        if cls._fernet is None:
            # Derivar una clave segura de 32 bytes URL-safe desde el SECRET_KEY
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=b'aspeia_static_salt',  # Salt fijo para reproducibilidad (trade-off aceptable aquí)
                iterations=100000,
            )
            key = base64.urlsafe_b64encode(kdf.derive(force_bytes(settings.SECRET_KEY)))
            cls._fernet = Fernet(key)
        return cls._fernet

    @classmethod
    def encrypt(cls, plaintext):
        if not plaintext:
            return None
        f = cls.get_fernet()
        return force_str(f.encrypt(force_bytes(plaintext)))

    @classmethod
    def decrypt(cls, ciphertext):
        if not ciphertext:
            return None
        f = cls.get_fernet()
        try:
            return force_str(f.decrypt(force_bytes(ciphertext)))
        except InvalidToken:
            logger.warning(
                "ModelEncryption.decrypt failed (InvalidToken). "
                "Ensure DJANGO_SECRET_KEY is the same in the web app and Celery worker (same .env)."
            )
            return None
        except Exception as e:
            logger.warning("ModelEncryption.decrypt failed: %s", e)
            return None
