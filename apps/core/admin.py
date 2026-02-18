from django.contrib import admin
from apps.core.models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """
    Admin para registros de auditoría.
    
    IMPORTANTE: Este admin es SOLO DE LECTURA.
    Los registros de auditoría NUNCA deben modificarse o eliminarse.
    """
    list_display = ('created_at', 'empresa', 'entity_type', 'action', 'user', 'ip_address')
    list_filter = ('action', 'entity_type', 'empresa', 'created_at')
    search_fields = ('entity_id', 'user__email', 'ip_address', 'notes')
    readonly_fields = (
        'empresa', 'entity_type', 'entity_id', 'action', 
        'payload_before', 'payload_after', 'user', 'ip_address', 
        'user_agent', 'notes', 'created_at'
    )
    date_hierarchy = 'created_at'
    ordering = ['-created_at']
    
    # Deshabilitar edición y eliminación
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False
    
    def has_delete_permission(self, request, obj=None):
        return False

