"""
Modelos para la integración con Odoo.
"""
from django.db import models
from apps.core.encryption import ModelEncryption


class OdooConnection(models.Model):
    """
    Configuración de conexión a instancia Odoo por empresa.
    
    Permite que cada empresa tenga su propia conexión Odoo,
    soportando escenarios multi-tenant donde cada cliente
    tiene su propia instancia de Odoo.
    """
    STATUS_CHOICES = [
        ('active', 'Activo'),
        ('inactive', 'Inactivo'),
        ('error', 'Error de conexión'),
    ]
    
    empresa = models.OneToOneField(
        'companies.Empresa',
        on_delete=models.CASCADE,
        related_name='odoo_connection',
        verbose_name='Empresa'
    )
    
    # Credenciales de Odoo
    odoo_url = models.URLField(
        verbose_name='URL de Odoo',
        help_text='Ej: http://localhost:8069 o https://mi-empresa.odoo.com'
    )
    odoo_db = models.CharField(
        max_length=100,
        verbose_name='Base de datos Odoo'
    )
    odoo_username = models.CharField(
        max_length=150,
        verbose_name='Usuario Odoo'
    )
    encrypted_password = models.TextField(
        verbose_name='Contraseña encriptada',
        help_text='Almacenada de forma segura usando Fernet'
    )
    
    # Mapeo de empresa
    odoo_company_id = models.IntegerField(
        verbose_name='ID Empresa en Odoo',
        help_text='res.company ID en Odoo'
    )
    
    # Estado y auditoría
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='active',
        verbose_name='Estado'
    )
    last_sync = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name='Última sincronización'
    )
    last_error = models.TextField(
        blank=True,
        null=True,
        verbose_name='Último error'
    )
    
    # Configuración de sincronización
    auto_sync_enabled = models.BooleanField(
        default=True,
        verbose_name='Sincronización automática',
        help_text='Sincronizar automáticamente al descargar CFDIs del SAT'
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Conexión Odoo'
        verbose_name_plural = 'Conexiones Odoo'
    
    def __str__(self):
        return f"{self.empresa.nombre} → {self.odoo_url}"
    
    def set_password(self, raw_password):
        """Encripta y guarda la contraseña."""
        self.encrypted_password = ModelEncryption.encrypt(raw_password)
    
    @property
    def password(self):
        """Desencripta y retorna la contraseña."""
        return ModelEncryption.decrypt(self.encrypted_password)


class OdooSyncLog(models.Model):
    """
    Log de sincronizaciones con Odoo.
    
    Registra cada intento de sincronización para auditoría
    y debugging.
    """
    STATUS_CHOICES = [
        ('pending', 'Pendiente'),
        ('success', 'Exitoso'),
        ('error', 'Error'),
        ('skipped', 'Omitido'),
    ]
    DIRECTION_CHOICES = [
        ('to_odoo', 'Hacia Odoo'),
        ('from_odoo', 'Desde Odoo'),
    ]
    
    connection = models.ForeignKey(
        OdooConnection,
        on_delete=models.CASCADE,
        related_name='sync_logs',
        verbose_name='Conexión'
    )
    cfdi_uuid = models.UUIDField(
        verbose_name='UUID del CFDI'
    )
    direction = models.CharField(
        max_length=20,
        choices=DIRECTION_CHOICES,
        default='to_odoo',
        verbose_name='Dirección'
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
        verbose_name='Estado'
    )
    
    # Resultado
    odoo_invoice_id = models.IntegerField(
        blank=True,
        null=True,
        verbose_name='ID Factura en Odoo',
        help_text='account.move ID si fue creada/encontrada'
    )
    action_taken = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name='Acción realizada',
        help_text='created, updated, verified, skipped'
    )
    error_message = models.TextField(
        blank=True,
        null=True,
        verbose_name='Mensaje de error'
    )
    
    # Metadata
    request_payload = models.JSONField(
        blank=True,
        null=True,
        verbose_name='Payload enviado'
    )
    response_data = models.JSONField(
        blank=True,
        null=True,
        verbose_name='Respuesta de Odoo'
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    
    class Meta:
        verbose_name = 'Log de Sincronización Odoo'
        verbose_name_plural = 'Logs de Sincronización Odoo'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['cfdi_uuid']),
            models.Index(fields=['connection', '-created_at']),
            models.Index(fields=['status']),
        ]
    
    def __str__(self):
        return f"{self.cfdi_uuid} - {self.get_status_display()}"
