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

    def __init__(self, url: str, db: str, username: str, password: str, allowed_company_id: int = None):
        """
        Inicializa el cliente Odoo.

        Args:
            url: URL base de Odoo (ej: http://localhost:8069)
            db: Nombre de la base de datos
            username: Usuario de Odoo
            password: Contraseña de Odoo
            allowed_company_id: ID de empresa para contexto multiempresa
        """
        self.url = url.rstrip('/')
        self.db = db
        self.username = username
        self.password = password
        self.allowed_company_id = allowed_company_id
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
            
            # Fetch user company_ids to use as fallback for context
            if not self.allowed_company_id and not hasattr(self, '_user_company_ids'):
                try:
                    user_data = self.models.execute_kw(
                        self.db, self.uid, self.password, 
                        'res.users', 'read', [[self.uid]], {'fields': ['company_ids']}
                    )
                    if user_data and user_data[0].get('company_ids'):
                        self._user_company_ids = user_data[0]['company_ids']
                except Exception as e:
                    logger.warning(f"No se pudieron obtener company_ids para UID {self.uid}: {e}")
                    self._user_company_ids = []
            
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
        
        # Inject standard multi-company context for Odoo 15+
        context = kwargs.get('context', {})
        needs_context_update = False
        
        if self.allowed_company_id:
            if 'allowed_company_ids' not in context:
                context['allowed_company_ids'] = [self.allowed_company_id]
                needs_context_update = True
        elif getattr(self, '_user_company_ids', None):
            if 'allowed_company_ids' not in context:
                context['allowed_company_ids'] = self._user_company_ids
                needs_context_update = True
                
        if needs_context_update:
            kwargs['context'] = context

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

    def create(self, model: str, values: dict, **kwargs) -> int:
        """Crea un nuevo registro. Acepta kwargs como context."""
        return self.execute_kw(model, 'create', [values], kwargs)

    def write(self, model: str, ids: list[int], values: dict, **kwargs) -> bool:
        """Actualiza registros existentes. Acepta kwargs como context."""
        return self.execute_kw(model, 'write', [ids, values], kwargs)

    def search_count(self, model: str, domain: list) -> int:
        """Cuenta registros que coinciden con el dominio."""
        return self.execute_kw(model, 'search_count', [domain])

    # ========== Métodos específicos para CFDI ==========

    def find_invoice_by_uuid(self, uuid: str, company_id: int = None) -> Optional[dict]:
        """Busca una factura por su UUID de CFDI (estándar + custom IT Admin)."""
        uuid_upper = uuid.upper()
        uuid_lower = uuid.lower()
        domain = [
            '|',
            ['l10n_mx_edi_cfdi_uuid', 'in', [uuid_upper, uuid_lower]],
            ['l10n_mx_edi_cfdi_uuid_cusom', 'in', [uuid_upper, uuid_lower]],
        ]
        if company_id:
            domain.append(['company_id', '=', company_id])

        try:
            invoices = self.search_read(
                'account.move',
                domain,
                fields=['id', 'name', 'l10n_mx_edi_cfdi_uuid', 'state', 'move_type',
                        'partner_id', 'amount_total', 'currency_id', 'invoice_date'],
                limit=1
            )
            return invoices[0] if invoices else None
        except OdooClientError:
            return None

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
                fields=['id', 'name', 'amount', 'tax_group_id'],
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
                fields=['id', 'name', 'amount', 'tax_group_id'],
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
        """
        Busca una factura por UUID usando múltiples métodos.

        Busca en orden:
        1. Campo estándar l10n_mx_edi_cfdi_uuid (account.move)
        2. Campo custom l10n_mx_edi_cfdi_uuid_cusom (account.move, módulo IT Admin)
        3. ir.attachment por cfdi_uuid (sin filtrar res_model, ya que se limpia)
        """
        uuid_upper = uuid.upper()
        uuid_lower = uuid.lower()

        move_fields = ['id', 'name', 'l10n_mx_edi_cfdi_uuid', 'state', 'move_type',
                       'partner_id', 'amount_total', 'currency_id', 'invoice_date',
                       'l10n_mx_edi_cfdi_state', 'l10n_mx_edi_cfdi_sat_state',
                       'attachment_id']

        # --- Búsqueda 1: Campo estándar l10n_mx_edi_cfdi_uuid ---
        domain = [
            '|',
            ['l10n_mx_edi_cfdi_uuid', '=', uuid_upper],
            ['l10n_mx_edi_cfdi_uuid', '=', uuid_lower],
        ]
        if company_id:
            domain.append(['company_id', '=', company_id])

        invoices = self.search_read('account.move', domain, fields=move_fields, limit=1)
        if invoices:
            return invoices[0]

        # --- Búsqueda 2: Campo custom l10n_mx_edi_cfdi_uuid_cusom (IT Admin) ---
        domain_custom = [
            '|',
            ['l10n_mx_edi_cfdi_uuid_cusom', '=', uuid_upper],
            ['l10n_mx_edi_cfdi_uuid_cusom', '=', uuid_lower],
        ]
        if company_id:
            domain_custom.append(['company_id', '=', company_id])

        try:
            invoices = self.search_read('account.move', domain_custom, fields=move_fields, limit=1)
            if invoices:
                return invoices[0]
        except OdooClientError:
            pass

        # --- Búsqueda 3: ir.attachment por cfdi_uuid (filtrado por empresa) ---
        # El mismo UUID puede existir legítimamente en otra empresa
        att_domain = [['cfdi_uuid', 'in', [uuid_upper, uuid_lower]]]
        if company_id:
            att_domain.append(['company_id', '=', company_id])
        try:
            attachments = self.search_read(
                'ir.attachment',
                att_domain,
                fields=['id', 'res_id', 'invoice_ids', 'cfdi_uuid'],
                limit=1
            )
            if attachments:
                att = attachments[0]
                # Intentar obtener la factura de invoice_ids o res_id
                inv_id = None
                if att.get('invoice_ids'):
                    inv_id = att['invoice_ids'][0]
                elif att.get('res_id'):
                    inv_id = att['res_id']

                if inv_id:
                    result = self.read('account.move', [inv_id], fields=move_fields)
                    if result:
                        return result[0]
        except OdooClientError:
            pass

        return None

    def create_cfdi_attachment(self, invoice_id: int, xml_content_base64: str,
                              uuid: str, filename: str = None,
                              company_id: int = None, cfdi_type: str = 'I',
                              estado: str = 'Vigente') -> int:
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
            'cfdi_type': cfdi_type,
            'estado': estado,
        }
        if company_id:
            attachment_vals['company_id'] = company_id
        try:
            attachment_vals['cfdi_uuid'] = uuid.upper()
        except Exception:
            pass
            
        attachment_id = self.create(
            'ir.attachment', 
            attachment_vals, 
            context={'is_fiel_attachment': True}
        )
        logger.info(f"Attachment CFDI creado: ID={attachment_id}, UUID={uuid}, Type={cfdi_type}")
        return attachment_id

    def update_invoice_attachment_link(self, invoice_id: int, attachment_id: int) -> bool:
        """Vincula una factura con su adjunto a través del campo técnico attachment_id."""
        return self.write('account.move', [invoice_id], {'attachment_id': attachment_id})

    def find_attachment_by_res_id(self, res_model: str, res_id: int, 
                                  filename_pattern: str = None) -> Optional[dict]:
        """Busca un adjunto por modelo y ID de registro."""
        domain = [
            ['res_model', '=', res_model],
            ['res_id', '=', res_id],
        ]
        if filename_pattern:
            domain.append(['name', 'ilike', filename_pattern])
            
        attachments = self.search_read(
            'ir.attachment',
            domain,
            fields=['id', 'name', 'cfdi_uuid', 'cfdi_total', 'date_cfdi'],
            limit=1
        )
        return attachments[0] if attachments else None

    def create_l10n_mx_edi_document(self, invoice_id: int, attachment_id: int,
                                     state: str = 'invoice_sent',
                                     sat_state: str = 'not_defined',
                                     cfdi_datetime: str = None) -> Optional[int]:
        """Crea un registro l10n_mx_edi.document para vincular el CFDI con la factura.

        Replica el patrón del módulo IT Admin (l10n_mx_sat_sync_itadmin_ee)
        que usa _create_update_invoice_document_from_invoice() con invoice_ids.
        """
        from datetime import datetime as dt
        if not cfdi_datetime:
            cfdi_datetime = dt.now().strftime('%Y-%m-%d %H:%M:%S')
        document_vals = {
            'move_id': invoice_id,
            'invoice_ids': [(6, 0, [invoice_id])],
            'attachment_id': attachment_id,
            'state': state,
            'sat_state': sat_state,
            'datetime': cfdi_datetime,
            'message': False,
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

    def get_product_account(self, product_id: int, is_supplier: bool, journal_id: int = None) -> Optional[int]:
        """Obtiene la cuenta contable de un producto, su categoría o el diario por defecto."""
        product = self.read('product.product', [product_id], 
                            ['property_account_expense_id', 'property_account_income_id', 'categ_id'])
        if not product:
            return None
        product = product[0]
        
        field = 'property_account_expense_id' if is_supplier else 'property_account_income_id'
        if product.get(field):
            return product[field][0]
            
        categ_id = product.get('categ_id')
        if categ_id:
            categ_field = 'property_account_expense_categ_id' if is_supplier else 'property_account_income_categ_id'
            categ = self.read('product.category', [categ_id[0]], [categ_field])
            if categ and categ[0].get(categ_field):
                return categ[0][categ_field][0]
                
        if journal_id:
            journal = self.read('account.journal', [journal_id], ['default_account_id'])
            if journal and journal[0].get('default_account_id'):
                return journal[0]['default_account_id'][0]
        return None

    def get_partner_account(self, partner_id: int, is_supplier: bool) -> Optional[int]:
        """Obtiene la cuenta por cobrar o por pagar de un partner."""
        field = 'property_account_payable_id' if is_supplier else 'property_account_receivable_id'
        partner = self.read('res.partner', [partner_id], [field])
        if partner and partner[0].get(field):
            return partner[0][field][0]
        return None


def create_client_from_connection(connection) -> OdooClient:
    """Crea un OdooClient desde un modelo OdooConnection prioritizando variables globales."""
    import os
    url = os.environ.get('ODOO_URL', '').strip() or connection.odoo_url
    db = os.environ.get('ODOO_DB', '').strip() or connection.odoo_db
    username = os.environ.get('ODOO_USERNAME', '').strip() or connection.odoo_username
    
    client = OdooClient(
        url=url,
        db=db,
        username=username,
        password=connection.password, # Este ya prioriza .env internamente
        allowed_company_id=connection.odoo_company_id
    )
    client.authenticate()
    return client
