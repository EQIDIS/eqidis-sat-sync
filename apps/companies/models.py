from django.db import models
from django.conf import settings


class Empresa(models.Model):
    """
    Tenant model representing a company in the multi-tenant system.
    This is the main entity that scopes all business data.
    """
    STATUS_CHOICES = [
        ('active', 'Activa'),
        ('suspended', 'Suspendida'),
    ]
    
    nombre = models.CharField(max_length=255, verbose_name='Nombre')
    razon_social = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        verbose_name='Razón Social',
        help_text='Nombre oficial para el SAT'
    )
    rfc = models.CharField(max_length=13, blank=True, null=True, verbose_name='RFC')
    codigo_postal = models.CharField(
        max_length=5,
        blank=True,
        null=True,
        verbose_name='Código Postal',
        help_text='Código postal del domicilio fiscal (requerido para CFDI 4.0)'
    )
    direccion = models.TextField(blank=True, null=True, verbose_name='Dirección')
    telefono = models.CharField(max_length=20, blank=True, null=True, verbose_name='Teléfono')
    email = models.EmailField(blank=True, null=True, verbose_name='Email de contacto')
    logo = models.ImageField(upload_to='empresas/logos/', blank=True, null=True, verbose_name='Logo')
    settings_json = models.JSONField(default=dict, blank=True, verbose_name='Configuración')
    
    # CFDI / SAT fields
    certificate = models.ForeignKey(
        'fiscal.CfdiCertificate',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='empresas',
        verbose_name='Certificado activo'
    )
    regimen_fiscal = models.ForeignKey(
        'fiscal.RegimenFiscal',
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name='empresas',
        verbose_name='Régimen Fiscal'
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='active',
        verbose_name='Estado'
    )
    last_mass_sync = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name='Última descarga masiva SAT'
    )
    
    is_active = models.BooleanField(default=True, verbose_name='Activa')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Fecha de creación')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Última actualización')
    
    class Meta:
        verbose_name = 'Empresa'
        verbose_name_plural = 'Empresas'
        ordering = ['nombre']
    
    def __str__(self):
        return self.nombre

    
    def get_members(self):
        """Returns all users that have access to this empresa."""
        return self.membresia_set.filter(is_active=True).select_related('usuario')
    
    def get_admin_members(self):
        """Returns all admin users of this empresa."""
        return self.membresia_set.filter(is_active=True, rol='admin').select_related('usuario')


class Membresia(models.Model):
    """
    Membership model linking users to companies with specific roles.
    This is the pivot table between CustomUser and Empresa.
    """
    ROLES = (
        ('admin', 'Administrador'),
        ('user', 'Usuario Estándar'),
    )
    
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='membresias',
        verbose_name='Usuario'
    )
    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.CASCADE,
        verbose_name='Empresa'
    )
    rol = models.CharField(
        max_length=20,
        choices=ROLES,
        default='user',
        verbose_name='Rol'
    )
    is_active = models.BooleanField(default=True, verbose_name='Activa')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Fecha de creación')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Última actualización')
    
    class Meta:
        verbose_name = 'Membresía'
        verbose_name_plural = 'Membresías'
        unique_together = ('usuario', 'empresa')
        ordering = ['empresa__nombre']
    
    def __str__(self):
        return f"{self.usuario.email} - {self.empresa.nombre} ({self.get_rol_display()})"
    
    def is_admin(self):
        """Check if this membership has admin role."""
        return self.rol == 'admin'
