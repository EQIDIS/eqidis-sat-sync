"""
Middleware de auditoría automática para el sistema.

Este middleware registra automáticamente acciones críticas en AuditLog.
"""
from django.utils.deprecation import MiddlewareMixin
from apps.core.models import AuditLog
from apps.core.utils import get_client_ip, get_user_agent


class AuditMiddleware(MiddlewareMixin):
    """
    Middleware que registra automáticamente acciones de login/logout.
    
    Para auditorías más específicas (crear CFDI, modificar pólizas, etc.)
    se recomienda usar AuditLog.log() directamente en las views/signals.
    
    Este middleware se enfoca en acciones de autenticación y seguridad.
    """
    
    def process_response(self, request, response):
        """
        Procesa la respuesta para detectar eventos de auditoría.
        """
        # Solo auditar usuarios autenticados
        if not hasattr(request, 'user') or not request.user.is_authenticated:
            return response
        
        # Detectar login/logout por URL patterns
        path = request.path
        
        # Login exitoso (redirección después de login)
        if '/accounts/login/' in path and response.status_code == 302:
            self._audit_login(request)
        
        # Logout
        elif '/accounts/logout/' in path:
            self._audit_logout(request)
        
        return response
    
    def _audit_login(self, request):
        """Registra evento de login."""
        # Solo si el usuario tiene empresa activa en sesión
        empresa_id = request.session.get('active_empresa_id')
        if not empresa_id:
            return
        
        try:
            from apps.companies.models import Empresa
            empresa = Empresa.objects.get(id=empresa_id)
            
            AuditLog.log(
                empresa=empresa,
                entity_type='CustomUser',
                entity_id=str(request.user.id),
                action='login',
                user=request.user,
                ip_address=get_client_ip(request),
                user_agent=get_user_agent(request),
                notes=f'Inicio de sesión exitoso para {request.user.email}'
            )
        except Exception:
            # Si falla la auditoría, no interrumpir el flujo normal
            pass
    
    def _audit_logout(self, request):
        """Registra evento de logout."""
        empresa_id = request.session.get('active_empresa_id')
        if not empresa_id:
            return
        
        try:
            from apps.companies.models import Empresa
            empresa = Empresa.objects.get(id=empresa_id)
            
            AuditLog.log(
                empresa=empresa,
                entity_type='CustomUser',
                entity_id=str(request.user.id),
                action='logout',
                user=request.user,
                ip_address=get_client_ip(request),
                user_agent=get_user_agent(request),
                notes=f'Cierre de sesión para {request.user.email}'
            )
        except Exception:
            pass
