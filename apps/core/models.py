from django.db import models
from django.conf import settings


class AuditLog(models.Model):
    """
    Auditoría general del sistema.
    
    CRÍTICO PARA COMPLIANCE:
    - Registra TODAS las acciones importantes (creación, modificación, eliminación)
    - Payload before/after permite reconstruir estado histórico
    - IP y usuario permiten rastrear origen de cambios
    - Indispensable para defensa fiscal y legal
    
    USO:
        AuditLog.objects.create(
            empresa=empresa,
            entity_type='CfdiDocument',
            entity_id=str(cfdi.uuid),
            action='status_change',
            payload_before={'estado': 'Vigente'},
            payload_after={'estado': 'Cancelado'},
            user=request.user,
            ip_address=get_client_ip(request)
        )
    """
    ACTION_CHOICES = [
        ('create', 'Creación'),
        ('update', 'Actualización'),
        ('delete', 'Eliminación'),
        ('status_change', 'Cambio de Estado'),
        ('import', 'Importación'),
        ('export', 'Exportación'),
        ('download', 'Descarga'),
        ('upload', 'Carga'),
        ('login', 'Inicio de Sesión'),
        ('logout', 'Cierre de Sesión'),
        ('permission_change', 'Cambio de Permisos'),
    ]
    
    empresa = models.ForeignKey(
        'companies.Empresa',
        on_delete=models.CASCADE,
        related_name='audit_logs',
        verbose_name='Empresa',
        help_text='Contexto de tenant obligatorio para multi-tenant estricto'
    )  # AJUSTE FINAL: Obligatorio para compliance
    entity_type = models.CharField(
        max_length=100,
        db_index=True,
        verbose_name='Tipo de entidad',
        help_text='Modelo Django afectado (ej: CfdiDocument, Poliza)'
    )
    entity_id = models.CharField(
        max_length=255,
        db_index=True,
        verbose_name='ID de entidad',
        help_text='PK del registro afectado'
    )
    action = models.CharField(
        max_length=50,
        choices=ACTION_CHOICES,
        db_index=True,
        verbose_name='Acción'
    )
    payload_before = models.JSONField(
        blank=True,
        null=True,
        verbose_name='Estado anterior',
        help_text='Snapshot del estado antes del cambio'
    )
    payload_after = models.JSONField(
        blank=True,
        null=True,
        verbose_name='Estado posterior',
        help_text='Snapshot del estado después del cambio'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='audit_logs',
        verbose_name='Usuario',
        help_text='Usuario que ejecutó la acción (null = sistema automático)'
    )
    ip_address = models.GenericIPAddressField(
        blank=True,
        null=True,
        verbose_name='Dirección IP',
        help_text='IP de origen de la acción'
    )
    user_agent = models.TextField(
        blank=True,
        null=True,
        verbose_name='User Agent',
        help_text='Navegador/cliente utilizado'
    )
    notes = models.TextField(
        blank=True,
        null=True,
        verbose_name='Notas',
        help_text='Contexto adicional de la acción'
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        verbose_name='Fecha y hora'
    )
    
    class Meta:
        verbose_name = 'Registro de Auditoría'
        verbose_name_plural = 'Registros de Auditoría'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['empresa', '-created_at']),
            models.Index(fields=['entity_type', 'entity_id', '-created_at']),
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['action', '-created_at']),
        ]
    
    def __str__(self):
        user_str = self.user.email if self.user else 'SISTEMA'
        return f"{user_str} - {self.get_action_display()} - {self.entity_type} [{self.entity_id}]"
    
    @classmethod
    def log(cls, empresa, entity_type, entity_id, action, user=None, ip_address=None, 
            payload_before=None, payload_after=None, notes=None, user_agent=None):
        """
        Helper method para crear logs fácilmente.
        
        Uso:
            AuditLog.log(
                empresa=empresa,
                entity_type='CfdiDocument',
                entity_id=str(cfdi_uuid),
                action='status_change',
                user=request.user,
                ip_address=get_client_ip(request),
                payload_before={'estado': 'Vigente'},
                payload_after={'estado': 'Cancelado'}
            )
        """
        return cls.objects.create(
            empresa=empresa,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            user=user,
            ip_address=ip_address,
            payload_before=payload_before,
            payload_after=payload_after,
            notes=notes,
            user_agent=user_agent
        )

