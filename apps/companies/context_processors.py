from django.conf import settings


def empresa_context(request):
    """
    Context processor to add current empresa and role to all templates.
    """
    context = {
        'current_empresa': getattr(request, 'empresa', None),
        'current_rol': getattr(request, 'rol_actual', None),
        'current_membresia': getattr(request, 'membresia', None),
    }
    
    # Add list of available empresas for authenticated users
    if request.user.is_authenticated:
        context['available_empresas'] = request.user.membresias.filter(
            is_active=True
        ).select_related('empresa')
    
    return context
