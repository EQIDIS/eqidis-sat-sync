from django.contrib import admin
from .models import (
    RegimenFiscal, UsoCfdi, FormaPago,
    CfdiCertificate, CfdiDocument, CfdiDownloadRequest, CfdiStateCheck,
    SatDownloadPackage, CfdiParserVersion, EmpresaSyncSettings
)


@admin.register(CfdiParserVersion)
class CfdiParserVersionAdmin(admin.ModelAdmin):
    """Admin para versiones de parser XSD."""
    list_display = ['cfdi_version', 'xsd_version', 'sat_release_date', 'valid_from', 'valid_to', 'is_active']
    list_filter = ['cfdi_version', 'is_active']
    search_fields = ['cfdi_version', 'xsd_version', 'notes']
    readonly_fields = ['created_at']
    ordering = ['-valid_from']
    
    fieldsets = (
        ('Versión', {
            'fields': ('cfdi_version', 'xsd_version', 'xsd_hash')
        }),
        ('Vigencia', {
            'fields': ('sat_release_date', 'valid_from', 'valid_to', 'is_active')
        }),
        ('Notas', {
            'fields': ('notes', 'created_at')
        }),
    )



@admin.register(RegimenFiscal)
class RegimenFiscalAdmin(admin.ModelAdmin):
    """Admin para catálogo de regímenes fiscales del SAT."""
    list_display = ['clave', 'descripcion', 'tipo_persona', 'is_active']
    list_filter = ['tipo_persona', 'is_active']
    search_fields = ['clave', 'descripcion']
    ordering = ['clave']


@admin.register(UsoCfdi)
class UsoCfdiAdmin(admin.ModelAdmin):
    """Admin para catálogo de usos de CFDI del SAT."""
    list_display = ['clave', 'descripcion', 'tipo_persona', 'is_active']
    list_filter = ['tipo_persona', 'is_active']
    search_fields = ['clave', 'descripcion']
    ordering = ['clave']


@admin.register(FormaPago)
class FormaPagoAdmin(admin.ModelAdmin):
    """Admin para catálogo de formas de pago del SAT."""
    list_display = ['clave', 'descripcion', 'is_active']
    list_filter = ['is_active']
    search_fields = ['clave', 'descripcion']
    ordering = ['clave']


@admin.register(CfdiCertificate)
class CfdiCertificateAdmin(admin.ModelAdmin):
    """Admin para gestión de certificados CFDI."""
    list_display = ['company', 'rfc', 'tipo', 'serial_number', 'valid_from', 'valid_to', 'status', 'created_at']
    list_filter = ['status', 'tipo', 'company']
    search_fields = ['rfc', 'serial_number']
    readonly_fields = ['created_at', 'updated_at', 'encrypted_password']
    ordering = ['-created_at']
    autocomplete_fields = ['company']
    
    fieldsets = (
        ('Identificación', {
            'fields': ('company', 'rfc', 'tipo', 'serial_number', 'status')
        }),
        ('Referencias Seguras (S3 / Encrypted DB)', {
            'fields': ('s3_cer_path', 's3_key_path', 'encrypted_password'),
            'description': '⚠️ Los archivos se almacenan en S3. La contraseña está encriptada en DB.'
        }),
        ('Vigencia', {
            'fields': ('valid_from', 'valid_to')
        }),
        ('Metadatos', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )


@admin.register(CfdiDocument)
class CfdiDocumentAdmin(admin.ModelAdmin):
    """Admin para documentos CFDI."""
    list_display = [
        'uuid', 'company', 'rfc_emisor', 'rfc_receptor', 
        'tipo_cfdi', 'metodo_pago', 'total', 'moneda', 'estado_sat', 'fecha_emision'
    ]
    list_filter = ['tipo_cfdi', 'metodo_pago', 'estado_sat', 'moneda', 'company']
    search_fields = ['uuid', 'rfc_emisor', 'rfc_receptor']
    date_hierarchy = 'fecha_emision'
    readonly_fields = ['uuid', 'created_at', 'last_state_check']
    ordering = ['-fecha_emision']
    autocomplete_fields = ['uso_cfdi', 'forma_pago']
    
    fieldsets = (
        ('Identificación', {
            'fields': ('uuid', 'company', 'tipo_cfdi')
        }),
        ('Partes', {
            'fields': ('rfc_emisor', 'rfc_receptor')
        }),
        ('Pago', {
            'fields': ('uso_cfdi', 'forma_pago', 'metodo_pago')
        }),
        ('Montos', {
            'fields': ('total', 'moneda')
        }),
        ('Estado SAT', {
            'fields': ('estado_sat', 'estado_cancelacion', 'fecha_cancelacion', 'last_state_check')
        }),
        ('Archivo S3', {
            'fields': ('s3_xml_path', 'xml_hash', 'xml_size'),
            'description': '⚠️ El XML se almacena en S3. Aquí solo hay referencias.'
        }),
        ('Fechas', {
            'fields': ('fecha_emision', 'created_at')
        }),
    )


@admin.register(CfdiDownloadRequest)
class CfdiDownloadRequestAdmin(admin.ModelAdmin):
    """Admin para solicitudes de descarga masiva."""
    list_display = ['id', 'company', 'tipo', 'fecha_inicio', 'fecha_fin', 'status', 'created_at', 'completed_at']
    list_filter = ['status', 'tipo', 'company']
    search_fields = ['request_id_sat', 'company__nombre']
    readonly_fields = ['created_at', 'completed_at']
    ordering = ['-created_at']
    
    fieldsets = (
        ('Solicitud', {
            'fields': ('company', 'tipo', 'request_id_sat')
        }),
        ('Rango de fechas', {
            'fields': ('fecha_inicio', 'fecha_fin')
        }),
        ('Estado', {
            'fields': ('status', 'created_at', 'completed_at')
        }),
    )


@admin.register(CfdiStateCheck)
class CfdiStateCheckAdmin(admin.ModelAdmin):
    """Admin para historial de verificaciones de estado."""
    list_display = ['document', 'estado_anterior', 'estado_sat', 'es_cambio', 'source', 'checked_at']
    list_filter = ['estado_sat', 'es_cambio', 'source']
    search_fields = ['document__uuid']
    readonly_fields = ['checked_at']
    ordering = ['-checked_at']
    
    fieldsets = (
        ('Documento', {
            'fields': ('document',)
        }),
        ('Estado', {
            'fields': ('estado_anterior', 'estado_sat', 'estado_cancelacion', 'es_cambio')
        }),
        ('Verificación', {
            'fields': ('source', 'checked_at')
        }),
        ('Debug', {
            'fields': ('response_raw',),
            'classes': ('collapse',),
        }),
    )


@admin.register(SatDownloadPackage)
class SatDownloadPackageAdmin(admin.ModelAdmin):
    """Admin para paquetes de descarga SAT."""
    list_display = ['package_id_sat', 'request', 'status', 'cfdi_processed', 'cfdi_count', 'retry_count', 'created_at']
    list_filter = ['status', 'request__company']
    search_fields = ['package_id_sat', 'request__request_id_sat']
    readonly_fields = ['created_at', 'completed_at', 'file_hash', 'file_size']
    ordering = ['-created_at']
    
    fieldsets = (
        ('Identificación', {
            'fields': ('request', 'package_id_sat', 'status')
        }),
        ('Archivo S3', {
            'fields': ('s3_zip_path', 'file_hash', 'file_size')
        }),
        ('Progreso', {
            'fields': ('cfdi_count', 'cfdi_processed', 'retry_count')
        }),
        ('Estado', {
            'fields': ('error_message', 'created_at', 'completed_at')
        }),
    )

@admin.register(EmpresaSyncSettings)
class EmpresaSyncSettingsAdmin(admin.ModelAdmin):
    """Admin para configuración de sincronización automática."""
    list_display = [
        'company', 'auto_sync_enabled', 'weekly_sync_enabled', 
        'sync_to_odoo_enabled', 'scheduled_start_hour', 
        'weekly_sync_day', 'weekly_sync_hour', 'last_sync_at', 'last_weekly_sync_at'
    ]
    list_filter = ['auto_sync_enabled', 'weekly_sync_enabled', 'sync_to_odoo_enabled']
    search_fields = ['company__nombre', 'company__rfc']
    ordering = ['company']
    
    fieldsets = (
        ('Sincronización Diaria (SAT)', {
            'fields': ('company', 'auto_sync_enabled', 'scheduled_start_hour'),
            'description': 'Configuración para descarga automática diaria de CFDIs del SAT.'
        }),
        ('Sincronización Semanal (SAT + Odoo)', {
            'fields': ('weekly_sync_enabled', 'weekly_sync_day', 'weekly_sync_hour', 'weekly_sync_minute', 'weekly_sync_days_range', 'sync_to_odoo_enabled'),
            'description': 'Configuración para descarga semanal y sincronización a Odoo.'
        }),
        ('Estado', {
            'fields': ('last_sync_at', 'last_weekly_sync_at')
        }),
    )
    readonly_fields = ['last_sync_at', 'last_weekly_sync_at']

