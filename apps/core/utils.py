"""
Utilidades para auditoría del sistema.
"""


def get_client_ip(request):
    """
    Extrae la IP real del cliente desde el request.
    
    Maneja correctamente proxies inversos (Nginx, Cloudflare, etc.)
    revisando los headers X-Forwarded-For y X-Real-IP.
    
    Args:
        request: Django HttpRequest object
        
    Returns:
        str: Dirección IP del cliente
        
    Usage:
        ip_address = get_client_ip(request)
        AuditLog.log(
            empresa=empresa,
            entity_type='CfdiDocument',
            entity_id=str(cfdi.uuid),
            action='create',
            user=request.user,
            ip_address=ip_address
        )
    """
    # Intenta X-Forwarded-For primero (proxies múltiples)
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        # X-Forwarded-For puede contener múltiples IPs separadas por coma
        # La primera es el cliente original
        ip = x_forwarded_for.split(',')[0].strip()
        return ip
    
    # Intenta X-Real-IP (proxy único como Nginx)
    x_real_ip = request.META.get('HTTP_X_REAL_IP')
    if x_real_ip:
        return x_real_ip.strip()
    
    # Fallback a REMOTE_ADDR (conexión directa)
    return request.META.get('REMOTE_ADDR', '0.0.0.0')


def get_user_agent(request):
    """
    Extrae el User-Agent del cliente.
    
    Args:
        request: Django HttpRequest object
        
    Returns:
        str: User-Agent string del navegador/cliente
    """
    return request.META.get('HTTP_USER_AGENT', 'Unknown')
