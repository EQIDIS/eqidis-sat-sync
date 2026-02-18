from django.db import models


class RegimenFiscal(models.Model):
    """
    Catálogo de Regímenes Fiscales del SAT.
    Basado en el catálogo c_RegimenFiscal del Anexo 20.
    
    Una empresa puede tener uno o más regímenes fiscales,
    pero debe tener al menos uno principal.
    
    CONTROL DE VIGENCIA:
    El SAT puede actualizar descripciones o deprecar claves.
    valid_from/valid_to permiten mantener histórico correcto.
    """
    PERSONA_CHOICES = [
        ('fisica', 'Persona Física'),
        ('moral', 'Persona Moral'),
        ('ambos', 'Ambos'),
    ]
    
    clave = models.CharField(
        max_length=3,
        unique=True,
        verbose_name='Clave SAT',
        help_text='Código del régimen según catálogo SAT'
    )
    descripcion = models.CharField(
        max_length=255,
        verbose_name='Descripción'
    )
    tipo_persona = models.CharField(
        max_length=10,
        choices=PERSONA_CHOICES,
        default='ambos',
        verbose_name='Tipo de persona'
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name='Activo'
    )
    # Vigencia temporal (cambios del SAT)
    valid_from = models.DateField(
        blank=True,
        null=True,
        verbose_name='Válido desde',
        help_text='Fecha desde la cual este régimen es válido según SAT'
    )
    valid_to = models.DateField(
        blank=True,
        null=True,
        verbose_name='Válido hasta',
        help_text='Fecha hasta la cual este régimen es válido (null = vigente)'
    )
    sat_version = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name='Versión catálogo SAT',
        help_text='Versión del catálogo del SAT (ej: 2024-01, 2025-06)'
    )

    class Meta:
        verbose_name = 'Régimen Fiscal'
        verbose_name_plural = 'Regímenes Fiscales'
        ordering = ['clave']

    def __str__(self):
        return f"{self.clave} - {self.descripcion}"


class UsoCfdi(models.Model):
    """
    Catálogo de Usos de CFDI del SAT.
    Basado en el catálogo c_UsoCFDI del Anexo 20.
    
    Define para qué se utilizará el comprobante fiscal.
    
    CONTROL DE VIGENCIA:
    El SAT actualiza este catálogo periódicamente.
    """
    PERSONA_CHOICES = [
        ('fisica', 'Persona Física'),
        ('moral', 'Persona Moral'),
        ('ambos', 'Ambos'),
    ]
    
    clave = models.CharField(
        max_length=4,
        unique=True,
        verbose_name='Clave SAT',
        help_text='Código del uso según catálogo SAT'
    )
    descripcion = models.CharField(
        max_length=255,
        verbose_name='Descripción'
    )
    tipo_persona = models.CharField(
        max_length=10,
        choices=PERSONA_CHOICES,
        default='ambos',
        verbose_name='Aplica a'
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name='Activo'
    )
    # Vigencia temporal (cambios del SAT)
    valid_from = models.DateField(
        blank=True,
        null=True,
        verbose_name='Válido desde'
    )
    valid_to = models.DateField(
        blank=True,
        null=True,
        verbose_name='Válido hasta'
    )
    sat_version = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name='Versión catálogo SAT'
    )

    class Meta:
        verbose_name = 'Uso de CFDI'
        verbose_name_plural = 'Usos de CFDI'
        ordering = ['clave']

    def __str__(self):
        return f"{self.clave} - {self.descripcion}"


class FormaPago(models.Model):
    """
    Catálogo de Formas de Pago del SAT.
    Basado en el catálogo c_FormaPago del Anexo 20.
    
    Define cómo se realizó o realizará el pago.
    
    CONTROL DE VIGENCIA:
    El SAT puede deprecar formas de pago o agregar nuevas.
    """
    clave = models.CharField(
        max_length=2,
        unique=True,
        verbose_name='Clave SAT',
        help_text='Código de la forma de pago según catálogo SAT'
    )
    descripcion = models.CharField(
        max_length=255,
        verbose_name='Descripción'
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name='Activo'
    )
    # Vigencia temporal (cambios del SAT)
    valid_from = models.DateField(
        blank=True,
        null=True,
        verbose_name='Válido desde'
    )
    valid_to = models.DateField(
        blank=True,
        null=True,
        verbose_name='Válido hasta'
    )
    sat_version = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name='Versión catálogo SAT'
    )

    class Meta:
        verbose_name = 'Forma de Pago'
        verbose_name_plural = 'Formas de Pago'
        ordering = ['clave']

    def __str__(self):
        return f"{self.clave} - {self.descripcion}"



class CfdiParserVersion(models.Model):
    """
    Versionado de parsers y XSD del SAT.
    
    CRÍTICO PARA LARGO PLAZO:
    Cuando el SAT actualiza reglas de validación, esquemas XSD o
    lógica de cancelación, necesitamos rastrear con qué versión
    se procesó cada CFDI.
    
    Esto permite:
    - Reprocesar CFDIs antiguos con reglas correctas
    - Justificar diferencias históricas
    - Migrar lógica de validación sin perder trazabilidad
    
    FUENTE:
    http://omawww.sat.gob.mx/tramitesyservicios/Paginas/anexo_20.htm
    """
    cfdi_version = models.CharField(
        max_length=10,
        db_index=True,
        verbose_name='Versión CFDI',
        help_text='ej: 3.3, 4.0'
    )
    xsd_version = models.CharField(
        max_length=50,
        verbose_name='Versión XSD',
        help_text='Versión del esquema XSD publicado por SAT'
    )
    xsd_hash = models.CharField(
        max_length=64,
        verbose_name='Hash SHA256 del XSD',
        help_text='Para validar que no fue modificado'
    )
    sat_release_date = models.DateField(
        verbose_name='Fecha de publicación SAT',
        help_text='Fecha en que el SAT publicó esta versión'
    )
    valid_from = models.DateField(
        verbose_name='Válido desde',
        help_text='Fecha desde la cual se debe usar esta versión'
    )
    valid_to = models.DateField(
        blank=True,
        null=True,
        verbose_name='Válido hasta',
        help_text='Fecha hasta la cual esta versión es válida (null = actual)'
    )
    notes = models.TextField(
        blank=True,
        null=True,
        verbose_name='Notas',
        help_text='Cambios significativos en esta versión'
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name='Activa'
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Fecha de creación'
    )

    class Meta:
        verbose_name = 'Versión Parser CFDI'
        verbose_name_plural = 'Versiones Parser CFDI'
        ordering = ['-valid_from']
        unique_together = ('cfdi_version', 'xsd_version')

    def __str__(self):
        return f"CFDI {self.cfdi_version} - XSD {self.xsd_version} (vigente desde {self.valid_from})"


class CfdiCertificate(models.Model):
    """
    Certificados FIEL/CSD para firma de CFDI.
    
    SEGURIDAD: Este modelo solo almacena METADATA y REFERENCIAS.
    - Los archivos .cer y .key se almacenan en S3 con cifrado KMS
    - La contraseña del .key se almacena en Secrets Manager
    
    Nunca guardar material criptográfico en la base de datos.
    
    Estructura S3 esperada:
    s3://cfdi-secrets/{company_id}/{rfc}/{tipo}/{fecha}.cer
    s3://cfdi-secrets/{company_id}/{rfc}/{tipo}/{fecha}.key
    """
    TIPO_CHOICES = [
        ('CSD', 'Certificado de Sello Digital'),
        ('FIEL', 'Firma Electrónica Avanzada'),
    ]
    STATUS_CHOICES = [
        ('active', 'Activo'),
        ('expired', 'Expirado'),
        ('revoked', 'Revocado'),
    ]
    
    company = models.ForeignKey(
        'companies.Empresa',
        on_delete=models.CASCADE,
        related_name='certificates',
        verbose_name='Empresa'
    )
    rfc = models.CharField(
        max_length=13,
        db_index=True,
        verbose_name='RFC'
    )
    tipo = models.CharField(
        max_length=4,
        choices=TIPO_CHOICES,
        default='CSD',
        verbose_name='Tipo de certificado'
    )
    # Referencias a S3 - NUNCA almacenar archivos en BD
    s3_cer_path = models.CharField(
        max_length=500,
        verbose_name='Ruta S3 del certificado (.cer)',
        help_text='ej: cfdi-secrets/123/RFC123/csd/2024-01.cer'
    )
    s3_key_path = models.CharField(
        max_length=500,
        verbose_name='Ruta S3 de la llave (.key)',
        help_text='ej: cfdi-secrets/123/RFC123/csd/2024-01.key'
    )
    # Contraseña encriptada (Fernet AES-128)
    encrypted_password = models.TextField(
        verbose_name='Contraseña encriptada',
        help_text='Almacenada de forma segura usando Fernet'
    )
    
    def set_password(self, raw_password):
        """Encripta y guarda la contraseña."""
        from apps.core.encryption import ModelEncryption
        self.encrypted_password = ModelEncryption.encrypt(raw_password)
        
    @property
    def password(self):
        """Desencripta y retorna la contraseña."""
        from apps.core.encryption import ModelEncryption
        return ModelEncryption.decrypt(self.encrypted_password)
    # Metadata del certificado (extraída del .cer)
    serial_number = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name='Número de serie'
    )
    valid_from = models.DateTimeField(
        verbose_name='Válido desde'
    )
    valid_to = models.DateTimeField(
        verbose_name='Válido hasta'
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='active',
        verbose_name='Estado'
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Fecha de creación'
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name='Última actualización'
    )

    class Meta:
        verbose_name = 'Certificado CFDI'
        verbose_name_plural = 'Certificados CFDI'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['rfc', 'status']),
        ]

    def __str__(self):
        return f"{self.rfc} ({self.get_status_display()}) - Válido hasta {self.valid_to.strftime('%Y-%m-%d')}"

    @property
    def is_valid(self):
        """Verifica si el certificado está vigente."""
        from django.utils import timezone
        now = timezone.now()
        return self.status == 'active' and self.valid_from <= now <= self.valid_to


class CfdiDocument(models.Model):
    """
    Documentos CFDI (facturas, notas de crédito, pagos, traslados).

    El company_id es obligatorio para multi-tenancy.
    El UUID no es PK porque el mismo CFDI puede existir para
    múltiples empresas (emisor y receptor).

    INMUTABILIDAD DE ESTADOS:
    Los campos estado_sat, estado_cancelacion, fecha_cancelacion son CACHE.
    La fuente de verdad es current_state_check FK.

    REGLA CRÍTICA:
    Nunca actualizar estado directamente.
    Siempre usar el método update_state() que:
    1. Crea registro en CfdiStateCheck
    2. Actualiza current_state_check
    3. Actualiza campos de cache
    """
    TIPO_CHOICES = [
        ('I', 'Ingreso'),
        ('E', 'Egreso'),
        ('P', 'Pago'),
        ('T', 'Traslado'),
    ]
    ESTADO_SAT_CHOICES = [
        ('Vigente', 'Vigente'),
        ('Cancelado', 'Cancelado'),
        ('No Encontrado', 'No Encontrado'),
    ]
    ESTADO_CANCELACION_CHOICES = [
        ('Cancelable sin aceptación', 'Cancelable sin aceptación'),
        ('Cancelable con aceptación', 'Cancelable con aceptación'),
        ('No cancelable', 'No cancelable'),
    ]
    METODO_PAGO_CHOICES = [
        ('PUE', 'Pago en Una sola Exhibición'),
        ('PPD', 'Pago en Parcialidades o Diferido'),
    ]
    CFDI_STATE_CHOICES = [
        ('draft', 'Borrador'),
        ('sent', 'Firmado/Enviado'),
        ('cancel_requested', 'Cancelación Solicitada'),
        ('cancel', 'Cancelado'),
        ('received', 'Recibido'),
        ('global_sent', 'Global Firmado'),
        ('global_cancel', 'Global Cancelado'),
    ]

    # PK autoincrement (permite mismo UUID en múltiples empresas)
    id = models.BigAutoField(primary_key=True)

    uuid = models.UUIDField(
        db_index=True,
        verbose_name='UUID'
    )
    company = models.ForeignKey(
        'companies.Empresa',
        on_delete=models.CASCADE,
        related_name='cfdi_documents',
        verbose_name='Empresa'
    )
    rfc_emisor = models.CharField(
        max_length=13,
        db_index=True,
        verbose_name='RFC Emisor'
    )
    rfc_receptor = models.CharField(
        max_length=13,
        db_index=True,
        verbose_name='RFC Receptor'
    )
    tipo_cfdi = models.CharField(
        max_length=1,
        choices=TIPO_CHOICES,
        verbose_name='Tipo de CFDI'
    )
    uso_cfdi = models.ForeignKey(
        UsoCfdi,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name='documentos',
        verbose_name='Uso de CFDI'
    )
    forma_pago = models.ForeignKey(
        FormaPago,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name='documentos',
        verbose_name='Forma de Pago'
    )
    metodo_pago = models.CharField(
        max_length=3,
        choices=METODO_PAGO_CHOICES,
        blank=True,
        null=True,
        verbose_name='Método de Pago',
        help_text='PUE = Una exhibición, PPD = Parcialidades'
    )
    total = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        verbose_name='Total'
    )
    moneda = models.CharField(
        max_length=3,
        default='MXN',
        verbose_name='Moneda'
    )
    fecha_emision = models.DateTimeField(
        db_index=True,
        verbose_name='Fecha de emisión'
    )

    # -------------------------------------------------------------------------
    # Campos adicionales del CFDI (Serie, Folio, Emisor, Receptor, Timbrado)
    # -------------------------------------------------------------------------

    # Identificación del comprobante
    serie = models.CharField(
        max_length=25,
        blank=True,
        null=True,
        verbose_name='Serie'
    )
    folio = models.CharField(
        max_length=40,
        blank=True,
        null=True,
        verbose_name='Folio'
    )

    # Datos del Emisor
    nombre_emisor = models.CharField(
        max_length=300,
        blank=True,
        null=True,
        verbose_name='Nombre Emisor'
    )
    regimen_fiscal_emisor = models.CharField(
        max_length=10,
        blank=True,
        null=True,
        verbose_name='Régimen Fiscal Emisor'
    )

    # Datos del Receptor
    nombre_receptor = models.CharField(
        max_length=300,
        blank=True,
        null=True,
        verbose_name='Nombre Receptor'
    )
    regimen_fiscal_receptor = models.CharField(
        max_length=10,
        blank=True,
        null=True,
        verbose_name='Régimen Fiscal Receptor'
    )
    domicilio_fiscal_receptor = models.CharField(
        max_length=10,
        blank=True,
        null=True,
        verbose_name='CP Receptor'
    )

    # Montos adicionales
    subtotal = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=0,
        verbose_name='Subtotal'
    )
    descuento = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        blank=True,
        null=True,
        verbose_name='Descuento'
    )

    # Timbrado
    fecha_timbrado = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name='Fecha Timbrado'
    )
    no_certificado_sat = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name='No. Certificado SAT'
    )

    # Estado del ciclo de vida CFDI (similar a Odoo l10n_mx_edi_cfdi_state)
    cfdi_state = models.CharField(
        max_length=20,
        choices=CFDI_STATE_CHOICES,
        default='received',
        verbose_name='Estado CFDI',
        help_text='Estado del ciclo de vida del CFDI'
    )

    # Indica si ya está vinculado a una factura/pago en otro sistema
    creado_en_sistema = models.BooleanField(
        default=False,
        verbose_name='Creado en Sistema',
        help_text='True si fue creado/vinculado en sistema contable'
    )

    # TRAZABILIDAD DE ORIGEN (para detección de duplicados y auditorías cruzadas)
    source = models.CharField(
        max_length=20,
        choices=[
            ('SAT', 'Descarga SAT'),
            ('Proveedor', 'Recibido de Proveedor'),
            ('CargaManual', 'Carga Manual'),
        ],
        default='SAT',
        verbose_name='Origen del CFDI',
        help_text='Fuente desde la cual se obtuvo este CFDI'
    )
    download_package = models.ForeignKey(
        'SatDownloadPackage',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='cfdis',
        verbose_name='Paquete de descarga SAT',
        help_text='Paquete SAT del cual proviene (si source=SAT)'
    )
    # INMUTABILIDAD: Estos campos son CACHE, la verdad está en current_state_check
    estado_sat = models.CharField(
        max_length=20,
        choices=ESTADO_SAT_CHOICES,
        default='Vigente',
        verbose_name='Estado SAT (cache)',
        help_text='CACHE - La fuente de verdad es current_state_check'
    )
    estado_cancelacion = models.CharField(
        max_length=50,
        choices=ESTADO_CANCELACION_CHOICES,
        blank=True,
        null=True,
        verbose_name='Estado de cancelación (cache)',
        help_text='CACHE - La fuente de verdad es current_state_check'
    )
    fecha_cancelacion = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name='Fecha de cancelación'
    )
    # FK a la verificación actual (fuente de verdad)
    current_state_check = models.ForeignKey(
        'CfdiStateCheck',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='+',
        verbose_name='Verificación de estado actual',
        help_text='FUENTE DE VERDAD - Última verificación oficial'
    )
    # Versionado de parser
    parser_version = models.ForeignKey(
        'CfdiParserVersion',
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name='documents',
        verbose_name='Versión de parser',
        help_text='Versión XSD con la que se procesó este CFDI'
    )
    # Referencia a S3 - Evitar FileField en BD
    s3_xml_path = models.CharField(
        max_length=500,
        blank=True,
        null=True,
        verbose_name='Ruta S3 del XML',
        help_text='ej: cfdi/xml/2024/01/UUID.xml'
    )
    xml_hash = models.CharField(
        max_length=64,
        blank=True,
        null=True,
        verbose_name='Hash SHA256 del XML',
        help_text='Para validar integridad post-descarga'
    )
    xml_size = models.PositiveIntegerField(
        blank=True,
        null=True,
        verbose_name='Tamaño del XML (bytes)'
    )
    last_state_check = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name='Última verificación de estado'
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Fecha de creación'
    )

    class Meta:
        verbose_name = 'Documento CFDI'
        verbose_name_plural = 'Documentos CFDI'
        ordering = ['-fecha_emision']
        indexes = [
            # Índices críticos para performance con millones de CFDIs
            models.Index(fields=['company', 'fecha_emision']),
            models.Index(fields=['company', 'rfc_emisor', 'fecha_emision']),
            models.Index(fields=['company', 'rfc_receptor', 'fecha_emision']),
            models.Index(fields=['company', 'tipo_cfdi']),
            models.Index(fields=['estado_sat']),
            models.Index(fields=['cfdi_state']),
            models.Index(fields=['last_state_check']),
        ]
        constraints = [
            models.UniqueConstraint(fields=['company', 'uuid'], name='unique_cfdi_per_company')
        ]

    def __str__(self):
        return f"{self.uuid} - {self.get_tipo_cfdi_display()} ${self.total} {self.moneda}"
    
    def update_state(self, nuevo_estado_sat, nuevo_estado_cancelacion=None, 
                     source='manual', user=None, response_raw=None):
        """
        Método SEGURO para actualizar el estado del CFDI.
        
        SIEMPRE usar este método en lugar de modificar estado_sat directamente.
        
        1. Crea registro en CfdiStateCheck
        2. Actualiza current_state_check FK
        3. Actualiza campos de cache
        
        Args:
            nuevo_estado_sat: 'Vigente', 'Cancelado', 'No Encontrado'
            nuevo_estado_cancelacion: opcional
            source: 'uuid_check', 'mass_download', 'manual', 'webhook'
            user: Usuario que ejecutó la acción (para auditoría)
            response_raw: Respuesta SAT completa
        
        Returns:
            CfdiStateCheck: Registro de la verificación creada
        """
        from apps.core.models import AuditLog
        
        estado_anterior = self.estado_sat
        es_cambio = (estado_anterior != nuevo_estado_sat)
        
        # 1. Crear registro en CfdiStateCheck
        state_check = CfdiStateCheck.objects.create(
            document=self,
            estado_anterior=estado_anterior,
            estado_sat=nuevo_estado_sat,
            estado_cancelacion=nuevo_estado_cancelacion,
            es_cambio=es_cambio,
            source=source,
            response_raw=response_raw
        )
        
        # 2. Actualizar current_state_check
        self.current_state_check = state_check
        self.estado_sat = nuevo_estado_sat
        self.estado_cancelacion = nuevo_estado_cancelacion
        self.last_state_check = state_check.checked_at
        
        if nuevo_estado_sat == 'Cancelado' and not self.fecha_cancelacion:
            self.fecha_cancelacion = state_check.checked_at
        
        self.save()
        
        # 3. Registrar en AuditLog si hubo cambio
        if es_cambio:
            AuditLog.log(
                empresa=self.company,
                entity_type='CfdiDocument',
                entity_id=str(self.uuid),
                action='status_change',
                user=user,
                payload_before={'estado_sat': estado_anterior},
                payload_after={'estado_sat': nuevo_estado_sat},
                notes=f"Cambio detectado por {source}"
            )
        
        return state_check
    
    def clean(self):
        """
        Validaciones de reglas de negocio SAT.
        
        REGLAS CFDI 4.0:
        - PPD (Pago en Parcialidades): forma_pago puede ser '99' (Por definir)
        - PUE (Pago en Una Exhibición): forma_pago debe ser específica (01-31, NO 99)
        """
        from django.core.exceptions import ValidationError
        
        super().clean()
        
        # Validar coherencia metodo_pago vs forma_pago
        if self.metodo_pago and self.forma_pago:
            forma_pago_clave = self.forma_pago.clave if hasattr(self.forma_pago, 'clave') else None
            
            if self.metodo_pago == 'PUE' and forma_pago_clave == '99':
                raise ValidationError({
                    'forma_pago': 'PUE (Pago en Una Exhibición) no puede usar forma de pago "99 - Por definir". '
                                  'Debe especificar una forma de pago concreta (01-31).'
                })
            
            # Advertencia informativa (no bloqueante) para PPD
            if self.metodo_pago == 'PPD' and forma_pago_clave != '99':
                # Esto es válido, pero inusual. No bloqueamos, solo documentamos el comportamiento esperado.
                # En PPD normalmente se usa '99' porque el pago real vendrá en los complementos de pago
                pass
    
    def save(self, *args, **kwargs):
        """Override save para ejecutar validaciones."""
        self.clean()
        super().save(*args, **kwargs)


class CfdiDownloadRequest(models.Model):
    """
    Solicitudes de descarga masiva al SAT.
    Tracking de estado y reintentos.
    
    INMUTABILIDAD:
    Una solicitud NUNCA se reutiliza, aunque el rango sea idéntico.
    Esto garantiza trazabilidad completa de cada descarga.
    """
    TIPO_CHOICES = [
        ('emitidos', 'Emitidos'),
        ('recibidos', 'Recibidos'),
    ]
    STATUS_CHOICES = [
        ('requested', 'Solicitado'),
        ('ready', 'Listo'),
        ('downloaded', 'Descargado'),
        ('failed', 'Fallido'),
    ]
    
    company = models.ForeignKey(
        'companies.Empresa',
        on_delete=models.CASCADE,
        related_name='cfdi_download_requests',
        verbose_name='Empresa'
    )
    # Trazabilidad de quién solicitó
    requested_by = models.ForeignKey(
        'users.CustomUser',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='cfdi_requests',
        verbose_name='Solicitado por',
        help_text='Usuario que inició la descarga (null = automático)'
    )
    request_id_sat = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name='ID de solicitud SAT'
    )
    is_auto_generated = models.BooleanField(
        default=False,
        verbose_name='Generación automática',
        help_text='True si fue generado por el worker de sincronización automática'
    )
    fecha_inicio = models.DateField(
        verbose_name='Fecha inicio'
    )
    fecha_fin = models.DateField(
        verbose_name='Fecha fin'
    )
    tipo = models.CharField(
        max_length=20,
        choices=TIPO_CHOICES,
        verbose_name='Tipo de descarga'
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='requested',
        verbose_name='Estado'
    )
    # Inmutabilidad y auditoría
    request_payload_hash = models.CharField(
        max_length=64,
        blank=True,
        null=True,
        verbose_name='Hash SHA256 del payload',
        help_text='Hash del request enviado al SAT (para detectar duplicados)'
    )
    sat_response_raw = models.TextField(
        blank=True,
        null=True,
        verbose_name='Respuesta SAT completa',
        help_text='Respuesta XML/JSON completa del SAT (debugging)'
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Fecha de creación'
    )
    completed_at = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name='Fecha de completado'
    )

    class Meta:
        verbose_name = 'Solicitud de descarga CFDI'
        verbose_name_plural = 'Solicitudes de descarga CFDI'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['company', 'status']),
            models.Index(fields=['company', '-created_at']),
            models.Index(fields=['requested_by', '-created_at']),
        ]

    def __str__(self):
        return f"{self.company} - {self.get_tipo_display()} ({self.fecha_inicio} a {self.fecha_fin})"


class CfdiStateCheck(models.Model):
    """
    Historial de verificaciones de estado de CFDI.
    Auditoría completa de cambios de estado con trazabilidad.
    
    Responde: ¿Cuándo cambió? ¿Quién lo detectó? ¿Fue SAT o reproceso?
    """
    SOURCE_CHOICES = [
        ('uuid_check', 'Verificación individual'),
        ('mass_download', 'Descarga masiva'),
        ('manual', 'Corrección manual'),
        ('webhook', 'Notificación SAT'),
    ]
    
    document = models.ForeignKey(
        CfdiDocument,
        on_delete=models.CASCADE,
        related_name='state_checks',
        verbose_name='Documento'
    )
    # TRAZABILIDAD CRIPTOGRÁFICA (reproducibilidad de consultas SAT)
    certificate = models.ForeignKey(
        'CfdiCertificate',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='state_checks',
        verbose_name='Certificado usado',
        help_text='CSD/FIEL con el que se realizó la consulta al SAT'
    )
    estado_anterior = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        verbose_name='Estado anterior',
        help_text='Estado antes de esta verificación'
    )
    estado_sat = models.CharField(
        max_length=20,
        verbose_name='Estado SAT'
    )
    estado_cancelacion = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name='Estado de cancelación'
    )
    es_cambio = models.BooleanField(
        default=False,
        verbose_name='¿Hubo cambio?',
        help_text='True si el estado cambió respecto al anterior'
    )
    source = models.CharField(
        max_length=20,
        choices=SOURCE_CHOICES,
        verbose_name='Fuente'
    )
    response_raw = models.TextField(
        blank=True,
        null=True,
        verbose_name='Respuesta SAT (raw)',
        help_text='Respuesta completa del SAT para debugging'
    )
    checked_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Fecha de verificación'
    )

    class Meta:
        verbose_name = 'Verificación de estado CFDI'
        verbose_name_plural = 'Verificaciones de estado CFDI'
        ordering = ['-checked_at']
        indexes = [
            # Índice crítico para queries de auditoría
            models.Index(fields=['document', '-checked_at']),
            models.Index(fields=['es_cambio', '-checked_at']),
            models.Index(fields=['source', '-checked_at']),
        ]

    def __str__(self):
        cambio = "→" if self.es_cambio else "="
        return f"{self.document.uuid} {self.estado_anterior or '?'}{cambio}{self.estado_sat}"


class SatDownloadPackage(models.Model):
    """
    Paquetes individuales de una solicitud de descarga SAT.
    Una solicitud puede generar múltiples paquetes.
    
    Permite:
    - Reintentar paquetes fallidos individualmente
    - Auditar estado de cada paquete
    - Escalar workers de procesamiento
    """
    STATUS_CHOICES = [
        ('pending', 'Pendiente'),
        ('downloading', 'Descargando'),
        ('downloaded', 'Descargado'),
        ('processing', 'Procesando'),
        ('completed', 'Completado'),
        ('failed', 'Fallido'),
    ]
    
    request = models.ForeignKey(
        CfdiDownloadRequest,
        on_delete=models.CASCADE,
        related_name='packages',
        verbose_name='Solicitud'
    )
    package_id_sat = models.CharField(
        max_length=100,
        verbose_name='ID del paquete SAT'
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
        verbose_name='Estado'
    )
    # Referencia a S3 - Evitar blobs en BD
    s3_zip_path = models.CharField(
        max_length=500,
        blank=True,
        null=True,
        verbose_name='Ruta S3 del ZIP',
        help_text='ej: cfdi/packages/2024/01/package_123.zip'
    )
    file_hash = models.CharField(
        max_length=64,
        blank=True,
        null=True,
        verbose_name='Hash SHA256'
    )
    file_size = models.PositiveIntegerField(
        blank=True,
        null=True,
        verbose_name='Tamaño (bytes)'
    )
    cfdi_count = models.PositiveIntegerField(
        default=0,
        verbose_name='CFDIs en el paquete'
    )
    cfdi_processed = models.PositiveIntegerField(
        default=0,
        verbose_name='CFDIs procesados'
    )
    error_message = models.TextField(
        blank=True,
        null=True,
        verbose_name='Mensaje de error'
    )
    retry_count = models.PositiveSmallIntegerField(
        default=0,
        verbose_name='Intentos'
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Fecha de creación'
    )
    completed_at = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name='Fecha de completado'
    )

    class Meta:
        verbose_name = 'Paquete de descarga SAT'
        verbose_name_plural = 'Paquetes de descarga SAT'
        ordering = ['-created_at']
        indexes = [
            # Índices para monitoreo de procesos asíncronos
            models.Index(fields=['request', 'status']),
            models.Index(fields=['status', '-created_at']),
        ]

    def __str__(self):
        return f"{self.package_id_sat} - {self.get_status_display()} ({self.cfdi_processed}/{self.cfdi_count})"

class EmpresaSyncSettings(models.Model):
    """
    Configuración de sincronización automática de CFDIs para una empresa.
    Determina si el worker debe descargar automáticamente las facturas.
    """
    company = models.OneToOneField(
        'companies.Empresa',
        on_delete=models.CASCADE,
        related_name='sync_settings',
        verbose_name='Empresa'
    )
    auto_sync_enabled = models.BooleanField(
        default=False,
        verbose_name='Sincronización automática activada',
        help_text='Si está activo, el sistema descargará facturas nuevas diariamente'
    )
    last_sync_at = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name='Última sincronización exitosa'
    )
    sync_frequency_hours = models.IntegerField(
        default=24,
        verbose_name='Frecuencia (horas)',
        help_text='DEPRECADO (Fijo a 24h). El sistema ahora sincroniza diariamente en la hora programada.'
    )
    scheduled_start_hour = models.IntegerField(
        default=3,
        verbose_name='Hora de inicio diaria (0-23)',
        help_text='Hora del día para ejecutar la sincronización diaria (0-23). Por defecto 3 AM.'
    )
    
    # Configuración de sincronización semanal
    weekly_sync_enabled = models.BooleanField(
        default=False,
        verbose_name='Sincronización semanal activada',
        help_text='Si está activo, el sistema descargará facturas de la semana anterior y sincronizará a Odoo'
    )
    weekly_sync_day = models.IntegerField(
        default=0,  # 0=Lunes
        verbose_name='Día de ejecución semanal (0=Lunes, 6=Domingo)',
        help_text='Día de la semana para ejecutar la sincronización semanal'
    )
    weekly_sync_hour = models.IntegerField(
        default=4,
        verbose_name='Hora de ejecución semanal (0-23)',
        help_text='Hora del día para ejecutar la sincronización semanal'
    )
    weekly_sync_minute = models.IntegerField(
        default=0,
        verbose_name='Minuto de ejecución semanal (0-59)',
        help_text='Minuto exacto para ejecutar la sincronización semanal'
    )
    weekly_sync_days_range = models.IntegerField(
        default=7,
        choices=[
            (7, 'Última semana (7 días)'),
            (14, 'Últimas 2 semanas (14 días)'),
            (21, 'Últimas 3 semanas (21 días)'),
            (30, 'Último mes (30 días)'),
            (60, 'Últimos 2 meses (60 días)'),
            (90, 'Últimos 3 meses (90 días)'),
            (120, 'Últimos 4 meses (120 días)'),
            (150, 'Últimos 5 meses (150 días)'),
            (180, 'Últimos 6 meses (180 días)'),
            (270, 'Últimos 9 meses (270 días)'),
            (365, 'Último año (365 días)'),
        ],
        verbose_name='Rango de días a descargar',
        help_text='Cuántos días hacia atrás descargar en cada sincronización'
    )
    sync_to_odoo_enabled = models.BooleanField(
        default=False,
        verbose_name='Sincronizar a Odoo',
        help_text='Si está activo, los CFDIs descargados se sincronizarán automáticamente a Odoo'
    )
    last_weekly_sync_at = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name='Última sincronización semanal exitosa'
    )
    
    class Meta:
        verbose_name = 'Configuración de Sincronización'
        verbose_name_plural = 'Configuraciones de Sincronización'
    
    def __str__(self):
        return f"SyncSettings: {self.company.nombre} ({'ON' if self.auto_sync_enabled else 'OFF'})"

