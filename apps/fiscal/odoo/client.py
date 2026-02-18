"""
Cliente XML-RPC para Odoo.

Encapsula la comunicación con Odoo a través de XML-RPC,
manejando autenticación, errores y reintentos.
"""
import xmlrpc.client
import logging
from typing import Optional, Any
from functools import wraps
import time

logger = logging.getLogger(__name__)


class OdooClientError(Exception):
    """Error genérico del cliente Odoo."""
    pass


class OdooAuthenticationError(OdooClientError):
    """Error de autenticación con Odoo."""
    pass


class OdooConnectionError(OdooClientError):
    """Error de conexión con Odoo."""
    pass


def retry_on_error(max_retries: int = 3, delay: float = 1.0):
    """Decorator para reintentar operaciones fallidas."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_error = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except (ConnectionError, TimeoutError, xmlrpc.client.Error) as e:
                    last_error = e
                    if attempt < max_retries - 1:
                        logger.warning(
                            f"Intento {attempt + 1}/{max_retries} falló: {e}. "
                            f"Reintentando en {delay}s..."
                        )
                        time.sleep(delay * (attempt + 1))
            raise OdooConnectionError(f"Falló después de {max_retries} intentos: {last_error}")
        return wrapper
    return decorator


class OdooClient:
    """
    Cliente XML-RPC para Odoo.

    Uso:
        client = OdooClient(url, db, username, password)
        client.authenticate()
        partners = client.search_read('res.partner', [['is_company', '=', True]])
    """

    def __init__(self, url: str, db: str, username: str, password: str):
        """
        Inicializa el cliente Odoo.

        Args:
            url: URL base de Odoo (ej: http://localhost:8069)
            db: Nombre de la base de datos
            username: Usuario de Odoo
            password: Contraseña de Odoo
        """
        self.url = url.rstrip('/')
        self.db = db
        self.username = username
        self.password = password
        self.uid: Optional[int] = None
        self._common: Optional[xmlrpc.client.ServerProxy] = None
        self._models: Optional[xmlrpc.client.ServerProxy] = None

    @property
    def common(self) -> xmlrpc.client.ServerProxy:
        """Proxy para el endpoint common de Odoo."""
        if self._common is None:
            self._common = xmlrpc.client.ServerProxy(
                f'{self.url}/xmlrpc/2/common',
                allow_none=True
            )
        return self._common

    @property
    def models(self) -> xmlrpc.client.ServerProxy:
        """Proxy para el endpoint object de Odoo."""
        if self._models is None:
            self._models = xmlrpc.client.ServerProxy(
                f'{self.url}/xmlrpc/2/object',
                allow_none=True
            )
        return self._models

    @retry_on_error(max_retries=3)
    def authenticate(self) -> int:
        """
        Autentica con Odoo y obtiene el UID.

        Returns:
            UID del usuario autenticado

        Raises:
            OdooAuthenticationError: Si las credenciales son inválidas
        """
        try:
            self.uid = self.common.authenticate(
                self.db, self.username, self.password, {}
            )
            if not self.uid:
                raise OdooAuthenticationError(
                    f"Credenciales inválidas para {self.username}@{self.db}"
                )
            logger.info(f"Autenticado en Odoo como UID={self.uid}")
            return self.uid
        except xmlrpc.client.Fault as e:
            raise OdooAuthenticationError(f"Error de autenticación: {e.faultString}")

    def _ensure_authenticated(self):
        """Verifica que el cliente esté autenticado."""
        if self.uid is None:
            self.authenticate()

    @retry_on_error(max_retries=2)
    def execute_kw(self, model: str, method: str, args: list, kwargs: dict = None) -> Any:
        """
        Ejecuta un método en un modelo de Odoo.

        Args:
            model: Nombre del modelo (ej: 'res.partner')
            method: Método a ejecutar (ej: 'search_read')
            args: Argumentos posicionales
            kwargs: Argumentos con nombre

        Returns:
            Resultado de la operación
        """
        self._ensure_authenticated()
        kwargs = kwargs or {}
        try:
            return self.models.execute_kw(
                self.db, self.uid, self.password,
                model, method, args, kwargs
            )
        except xmlrpc.client.Fault as e:
            logger.error(f"Error ejecutando {model}.{method}: {e.faultString}")
            raise OdooClientError(f"Error en {model}.{method}: {e.faultString}")

    # ========== Métodos de conveniencia ==========

    def search(self, model: str, domain: list, **kwargs) -> list[int]:
        """Busca IDs de registros."""
        return self.execute_kw(model, 'search', [domain], kwargs)

    def search_read(self, model: str, domain: list, fields: list = None, **kwargs) -> list[dict]:
        """Busca y lee registros en una sola llamada."""
        kwargs['fields'] = fields or []
        return self.execute_kw(model, 'search_read', [domain], kwargs)

    def read(self, model: str, ids: list[int], fields: list = None) -> list[dict]:
        """Lee registros por sus IDs."""
        return self.execute_kw(model, 'read', [ids], {'fields': fields or []})

    def create(self, model: str, values: dict) -> int:
        """Crea un nuevo registro."""
        return self.execute_kw(model, 'create', [values])

    def write(self, model: str, ids: list[int], values: dict) -> bool:
        """Actualiza registros existentes."""
        return self.execute_kw(model, 'write', [ids, values])

    def search_count(self, model: str, domain: list) -> int:
        """Cuenta registros que coinciden con el dominio."""
        return self.execute_kw(model, 'search_count', [domain])

    # ========== Métodos específicos para CFDI ==========

    def find_invoice_by_uuid(self, uuid: str, company_id: int = None) -> Optional[dict]:
        """Busca una factura por su UUID de CFDI."""
        domain = [['l10n_mx_edi_cfdi_uuid', '=ilike', uuid]]
        if company_id:
            domain.append(['company_id', '=', company_id])

        invoices = self.search_read(
            'account.move',
            domain,
            fields=['id', 'name', 'l10n_mx_edi_cfdi_uuid', 'state', 'move_type',
                    'partner_id', 'amount_total', 'currency_id', 'invoice_date']
        )
        return invoices[0] if invoices else None

    def find_partner_by_vat(self, vat: str, company_id: int = None) -> Optional[dict]:
        """Busca un partner por su RFC/VAT."""
        domain = [['vat', '=ilike', vat]]
        if company_id:
            domain.append('|')
            domain.append(['company_id', '=', company_id])
            domain.append(['company_id', '=', False])

        partners = self.search_read(
            'res.partner',
            domain,
            fields=['id', 'name', 'vat', 'company_type'],
            limit=1
        )
        return partners[0] if partners else None

    def find_tax_by_amount(self, amount: float, tax_type: str = 'purchase',
                           company_id: int = None) -> Optional[dict]:
        """Busca un impuesto por su porcentaje."""
        return self.find_tax_extended(amount, tax_type, company_id)

    def find_tax_extended(self, amount: float, tax_type: str, company_id: int = None,
                          sat_code: str = None, factor_type: str = None) -> Optional[dict]:
        """Busca un impuesto con criterios extendidos (SAT)."""
        domain = [
            ['amount', '=', amount],
            ['type_tax_use', '=', tax_type],
            ['company_id', '=', company_id],
        ]
        if sat_code:
            domain.append('|')
            domain.append(['impuesto', '=', sat_code])
            domain.append(['l10n_mx_tax_type', '=', sat_code])
        if factor_type:
            domain.append(['l10n_mx_factor_type', '=', factor_type])

        try:
            taxes = self.search_read(
                'account.tax',
                domain,
                fields=['id', 'name', 'amount'],
                limit=1
            )
            if taxes:
                return taxes[0]
        except OdooClientError:
            domain = [
                ['amount', '=', amount],
                ['type_tax_use', '=', tax_type],
                ['company_id', '=', company_id],
            ]
            taxes = self.search_read(
                'account.tax',
                domain,
                fields=['id', 'name', 'amount'],
                limit=1
            )
            if taxes:
                return taxes[0]
        return None

    def create_tax(self, vals: dict) -> int:
        """Crea un nuevo impuesto."""
        return self.create('account.tax', vals)

    def get_invoice_attachment(self, invoice_id: int, attachment_type: str = 'xml') -> Optional[dict]:
        """Obtiene el attachment XML o PDF de una factura."""
        extension = '.xml' if attachment_type == 'xml' else '.pdf'
        domain = [
            ['res_model', '=', 'account.move'],
            ['res_id', '=', invoice_id],
            ['mimetype', 'ilike', 'xml' if attachment_type == 'xml' else 'pdf']
        ]
        attachments = self.search_read(
            'ir.attachment',
            domain,
            fields=['id', 'name', 'datas', 'mimetype', 'file_size']
        )
        for att in attachments:
            if att['name'].lower().endswith(extension):
                return att
        return attachments[0] if attachments else None

    def get_version(self) -> dict:
        """Obtiene información de versión de Odoo."""
        return self.common.version()

    def get_companies(self) -> list[dict]:
        """
        Lista todas las empresas (res.company) en Odoo.
        Para uso en entornos multiempresa: el usuario elige a cuál vincular cada Empresa de Aspeia.

        Returns:
            Lista de dict con 'id' y 'name' de cada res.company.
        """
        companies = self.search_read(
            'res.company',
            [],
            fields=['id', 'name'],
            order='name asc',
        )
        return companies or []

    # ========== Métodos para sincronización CFDI completa (Odoo 18) ==========

    def find_invoice_by_uuid_extended(self, uuid: str, company_id: int = None) -> Optional[dict]:
        """Busca una factura por UUID usando múltiples métodos (Odoo 18)."""
        uuid_upper = uuid.upper()
        uuid_lower = uuid.lower()
        domain = [
            '|', '|',
            ['l10n_mx_edi_cfdi_uuid', '=', uuid_upper],
            ['l10n_mx_edi_cfdi_uuid', '=', uuid_lower],
            ['l10n_mx_edi_cfdi_uuid', '=ilike', uuid]
        ]
        if company_id:
            domain.append(['company_id', '=', company_id])

        invoices = self.search_read(
            'account.move',
            domain,
            fields=['id', 'name', 'l10n_mx_edi_cfdi_uuid', 'state', 'move_type',
                    'partner_id', 'amount_total', 'currency_id', 'invoice_date',
                    'l10n_mx_edi_cfdi_state', 'l10n_mx_edi_cfdi_sat_state'],
            limit=1
        )
        if invoices:
            return invoices[0]

        doc_domain = [
            '|', '|',
            ['attachment_uuid', '=', uuid_upper],
            ['attachment_uuid', '=', uuid_lower],
            ['attachment_uuid', '=ilike', uuid]
        ]
        try:
            docs = self.search_read(
                'l10n_mx_edi.document',
                doc_domain,
                fields=['id', 'move_id', 'attachment_uuid', 'state', 'sat_state'],
                limit=1
            )
            if docs and docs[0].get('move_id'):
                move_id = docs[0]['move_id'][0] if isinstance(docs[0]['move_id'], (list, tuple)) else docs[0]['move_id']
                return self.read('account.move', [move_id],
                    fields=['id', 'name', 'l10n_mx_edi_cfdi_uuid', 'state', 'move_type',
                            'partner_id', 'amount_total', 'l10n_mx_edi_cfdi_state',
                            'l10n_mx_edi_cfdi_sat_state'])[0]
        except OdooClientError:
            pass

        att_domain = [
            ['res_model', '=', 'account.move'],
            '|', '|',
            ['cfdi_uuid', '=', uuid_upper],
            ['cfdi_uuid', '=', uuid_lower],
            ['cfdi_uuid', '=ilike', uuid]
        ]
        try:
            attachments = self.search_read(
                'ir.attachment',
                att_domain,
                fields=['id', 'res_id', 'cfdi_uuid'],
                limit=1
            )
            if attachments and attachments[0].get('res_id'):
                return self.read('account.move', [attachments[0]['res_id']],
                    fields=['id', 'name', 'l10n_mx_edi_cfdi_uuid', 'state', 'move_type',
                            'partner_id', 'amount_total', 'l10n_mx_edi_cfdi_state',
                            'l10n_mx_edi_cfdi_sat_state'])[0]
        except OdooClientError:
            pass
        return None

    def create_cfdi_attachment(self, invoice_id: int, xml_content_base64: str,
                              uuid: str, filename: str = None,
                              company_id: int = None) -> int:
        """Crea un ir.attachment con el XML del CFDI vinculado a una factura."""
        if not filename:
            filename = f"{uuid.upper()}.xml"
        attachment_vals = {
            'name': filename,
            'datas': xml_content_base64,
            'res_model': 'account.move',
            'res_id': invoice_id,
            'mimetype': 'application/xml',
            'type': 'binary',
        }
        if company_id:
            attachment_vals['company_id'] = company_id
        try:
            attachment_vals['cfdi_uuid'] = uuid.upper()
        except Exception:
            pass
        attachment_id = self.create('ir.attachment', attachment_vals)
        logger.info(f"Attachment CFDI creado: ID={attachment_id}, UUID={uuid}")
        return attachment_id

    def create_l10n_mx_edi_document(self, invoice_id: int, attachment_id: int,
                                     state: str = 'invoice_received',
                                     sat_state: str = 'not_defined',
                                     cfdi_datetime: str = None) -> Optional[int]:
        """Crea un registro l10n_mx_edi.document para vincular el CFDI con la factura."""
        from datetime import datetime as dt
        if not cfdi_datetime:
            cfdi_datetime = dt.now().strftime('%Y-%m-%d %H:%M:%S')
        document_vals = {
            'move_id': invoice_id,
            'attachment_id': attachment_id,
            'state': state,
            'sat_state': sat_state,
            'datetime': cfdi_datetime,
        }
        try:
            doc_id = self.create('l10n_mx_edi.document', document_vals)
            logger.info(f"l10n_mx_edi.document creado: ID={doc_id}, move={invoice_id}, state={state}")
            return doc_id
        except OdooClientError as e:
            logger.warning(f"No se pudo crear l10n_mx_edi.document: {e}")
            return None

    def post_invoice(self, invoice_id: int) -> bool:
        """Publica una factura (cambia de draft a posted)."""
        try:
            self.execute_kw('account.move', 'action_post', [[invoice_id]])
            logger.info(f"Factura {invoice_id} publicada exitosamente")
            return True
        except OdooClientError as e:
            logger.error(f"Error publicando factura {invoice_id}: {e}")
            return False

    def update_cfdi_document_state(self, invoice_id: int, sat_state: str) -> bool:
        """Actualiza el estado SAT de un documento CFDI existente."""
        try:
            docs = self.search_read(
                'l10n_mx_edi.document',
                [['move_id', '=', invoice_id]],
                fields=['id'],
                limit=1
            )
            if docs:
                self.write('l10n_mx_edi.document', [docs[0]['id']], {'sat_state': sat_state})
                logger.info(f"Estado SAT actualizado para factura {invoice_id}: {sat_state}")
                return True
            return False
        except OdooClientError as e:
            logger.warning(f"No se pudo actualizar estado SAT: {e}")
            return False


def create_client_from_connection(connection) -> OdooClient:
    """Crea un OdooClient desde un modelo OdooConnection."""
    client = OdooClient(
        url=connection.odoo_url,
        db=connection.odoo_db,
        username=connection.odoo_username,
        password=connection.password
    )
    client.authenticate()
    return client
