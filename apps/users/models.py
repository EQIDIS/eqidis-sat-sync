from django.db import models
from django.contrib.auth.models import AbstractUser


class CustomUser(AbstractUser):
    """
    Custom user model for multi-tenant SaaS.
    Uses email as the primary identifier instead of username.
    """
    email = models.EmailField(unique=True)
    
    # Make email the login identifier
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']  # username is still required for AbstractUser
    
    class Meta:
        verbose_name = 'Usuario'
        verbose_name_plural = 'Usuarios'
    
    def __str__(self):
        return self.email
    
    def get_empresas(self):
        """Returns all companies (Empresas) this user has access to."""
        from apps.companies.models import Empresa
        return Empresa.objects.filter(membresia__usuario=self, membresia__is_active=True)
    
    def get_membresias(self):
        """Returns all active memberships for this user."""
        return self.membresias.filter(is_active=True)
    
    def get_rol_for_empresa(self, empresa):
        """Returns the role for a specific empresa, or None if no membership."""
        membresia = self.membresias.filter(empresa=empresa, is_active=True).first()
        return membresia.rol if membresia else None
