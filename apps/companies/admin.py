from django.contrib import admin
from .models import Empresa, Membresia


@admin.register(Empresa)
class EmpresaAdmin(admin.ModelAdmin):
    """
    Admin configuration for Empresa (Company) model.
    """
    list_display = ('nombre', 'razon_social', 'rfc', 'regimen_fiscal', 'status', 'certificate', 'last_mass_sync', 'is_active', 'created_at')
    list_filter = ('is_active', 'status', 'regimen_fiscal', 'created_at')
    search_fields = ('nombre', 'razon_social', 'rfc', 'email')
    ordering = ('nombre',)
    readonly_fields = ('created_at', 'updated_at', 'last_mass_sync')
    autocomplete_fields = ['certificate', 'regimen_fiscal']
    
    fieldsets = (
        (None, {
            'fields': ('nombre', 'razon_social', 'rfc', 'codigo_postal', 'is_active', 'status')
        }),
        ('CFDI / SAT', {
            'fields': ('regimen_fiscal', 'certificate', 'last_mass_sync'),
            'description': 'Configuración para facturación electrónica'
        }),
        ('Contacto', {
            'fields': ('direccion', 'telefono', 'email')
        }),
        ('Branding', {
            'fields': ('logo',),
            'classes': ('collapse',)
        }),
        ('Configuración', {
            'fields': ('settings_json',),
            'classes': ('collapse',)
        }),
        ('Metadatos', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


class MembresiaInline(admin.TabularInline):
    """
    Inline for managing memberships from Empresa admin.
    """
    model = Membresia
    extra = 1
    autocomplete_fields = ['usuario']


# Add inline to EmpresaAdmin
EmpresaAdmin.inlines = [MembresiaInline]


@admin.register(Membresia)
class MembresiaAdmin(admin.ModelAdmin):
    """
    Admin configuration for Membresia (Membership) model.
    """
    list_display = ('usuario', 'empresa', 'rol', 'is_active', 'created_at')
    list_filter = ('rol', 'is_active', 'empresa')
    search_fields = ('usuario__email', 'usuario__username', 'empresa__nombre')
    autocomplete_fields = ['usuario', 'empresa']
    readonly_fields = ('created_at', 'updated_at')
    ordering = ('empresa__nombre', 'usuario__email')
    
    fieldsets = (
        (None, {
            'fields': ('usuario', 'empresa', 'rol', 'is_active')
        }),
        ('Metadatos', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
