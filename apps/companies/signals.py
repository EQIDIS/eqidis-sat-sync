from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings
from .models import Empresa, Membresia


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_default_empresa_for_new_user(sender, instance, created, **kwargs):
    """
    Signal to create a default Empresa and Membresia when a new user registers.
    This implements the onboarding flow where each new user gets their own company.
    
    This behavior can be customized:
    - Set instance._skip_auto_empresa = True before save to skip this
    - Or override in a custom registration view
    """
    # Skip if explicitly flagged
    if getattr(instance, '_skip_auto_empresa', False):
        return
    
    # Skip for non-new users
    if not created:
        return
    
    # Skip for superusers created via createsuperuser command
    # (they typically manage the platform, not a specific company)
    if instance.is_superuser:
        return
    
    # Create a default empresa for the new user
    empresa = Empresa.objects.create(
        nombre=f"Empresa de {instance.email.split('@')[0]}",
    )
    
    # Create admin membership for the new user
    Membresia.objects.create(
        usuario=instance,
        empresa=empresa,
        rol='admin',
        is_active=True,
    )
