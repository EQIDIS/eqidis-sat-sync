"""
Admin para el módulo de integración Odoo.
"""
from django.contrib import admin
from django.utils.html import format_html
from .models import OdooConnection, OdooSyncLog


@admin.register(OdooConnection)
class OdooConnectionAdmin(admin.ModelAdmin):
    list_display = ['empresa', 'odoo_url', 'odoo_company_id', 'status_badge', 
                    'auto_sync_enabled', 'last_sync']
    list_filter = ['status', 'auto_sync_enabled']
    search_fields = ['empresa__nombre', 'empresa__rfc', 'odoo_url']
    readonly_fields = ['last_sync', 'last_error', 'created_at', 'updated_at']
    
    fieldsets = (
        ('Empresa', {
            'fields': ('empresa',)
        }),
        ('Conexión Odoo', {
            'fields': ('odoo_url', 'odoo_db', 'odoo_username', 'odoo_company_id')
        }),
        ('Contraseña', {
            'fields': ('encrypted_password',),
            'description': 'La contraseña se almacena encriptada.'
        }),
        ('Configuración', {
            'fields': ('status', 'auto_sync_enabled')
        }),
        ('Auditoría', {
            'fields': ('last_sync', 'last_error', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def status_badge(self, obj):
        colors = {
            'active': 'green',
            'inactive': 'gray',
            'error': 'red',
        }
        color = colors.get(obj.status, 'gray')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; '
            'border-radius: 3px; font-size: 11px;">{}</span>',
            color, obj.get_status_display()
        )
    status_badge.short_description = 'Estado'
    
    def save_model(self, request, obj, form, change):
        # Si se proporciona contraseña en texto plano, encriptarla
        password = form.cleaned_data.get('encrypted_password', '')
        if password and not password.startswith('gAAAAA'):  # No está encriptada (Fernet prefix)
            obj.set_password(password)
        super().save_model(request, obj, form, change)


@admin.register(OdooSyncLog)
class OdooSyncLogAdmin(admin.ModelAdmin):
    list_display = ['cfdi_uuid', 'connection', 'status_badge', 'action_taken', 
                    'odoo_invoice_id', 'created_at']
    list_filter = ['status', 'action_taken', 'direction', 'connection']
    search_fields = ['cfdi_uuid']
    readonly_fields = ['connection', 'cfdi_uuid', 'direction', 'status', 
                       'odoo_invoice_id', 'action_taken', 'error_message',
                       'request_payload', 'response_data', 'created_at', 'completed_at']
    
    def status_badge(self, obj):
        colors = {
            'pending': 'orange',
            'success': 'green',
            'error': 'red',
            'skipped': 'gray',
        }
        color = colors.get(obj.status, 'gray')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; '
            'border-radius: 3px; font-size: 11px;">{}</span>',
            color, obj.get_status_display()
        )
    status_badge.short_description = 'Estado'
    
    def has_add_permission(self, request):
        return False  # Los logs se crean automáticamente
    
    def has_change_permission(self, request, obj=None):
        return False  # Los logs son inmutables
