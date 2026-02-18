from django.shortcuts import redirect
from django.urls import reverse
from django.conf import settings
from django_multitenant.utils import set_current_tenant, unset_current_tenant


class TenantMiddleware:
    """
    Middleware for multi-tenant context management.
    
    After allauth authenticates the user, this middleware:
    1. Reads the active company from session
    2. Verifies the user has membership in that company
    3. Sets the tenant context for all subsequent queries
    4. Redirects to company selector if no company is selected
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        # Always unset tenant at the start
        unset_current_tenant()
        
        # Get exempt paths from settings
        exempt_paths = getattr(settings, 'TENANT_EXEMPT_PATHS', [])
        
        # Check if current path is exempt from tenant requirement
        if self._is_exempt_path(request.path, exempt_paths):
            return self.get_response(request)
        
        # Only apply tenant logic for authenticated users
        if request.user.is_authenticated:
            session_key = getattr(settings, 'TENANT_SESSION_KEY', 'active_empresa_id')
            empresa_id = request.session.get(session_key)
            
            if empresa_id:
                # Verify user has access to this company
                from apps.companies.models import Membresia
                membresia = request.user.membresias.filter(
                    empresa_id=empresa_id,
                    is_active=True
                ).select_related('empresa').first()
                
                if membresia:
                    # Set tenant context for all queries
                    set_current_tenant(membresia.empresa)
                    # Attach empresa and role to request for easy access
                    request.empresa = membresia.empresa
                    request.rol_actual = membresia.rol
                    request.membresia = membresia
                else:
                    # User doesn't have access to this company anymore
                    del request.session[session_key]
                    return redirect('companies:seleccionar_empresa')
            else:
                # No company selected, redirect to selector
                return redirect('companies:seleccionar_empresa')
        
        return self.get_response(request)
    
    def _is_exempt_path(self, path, exempt_paths):
        """Check if the request path is exempt from tenant requirement."""
        return any(path.startswith(exempt) for exempt in exempt_paths)
