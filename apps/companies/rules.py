import rules

# =============================================================================
# Predicates for multi-tenant permission system
# =============================================================================

@rules.predicate
def is_empresa_member(user, obj=None):
    """
    Check if the user is a member of any active empresa.
    """
    if not user.is_authenticated:
        return False
    return user.membresias.filter(is_active=True).exists()


@rules.predicate
def is_empresa_admin(user, obj=None):
    """
    Check if the user is an admin of the current empresa (from request context).
    Note: This requires the request object to have 'rol_actual' set by TenantMiddleware.
    For object-level checks, use is_admin_of_empresa.
    """
    if not user.is_authenticated:
        return False
    # Check if user has admin role in any empresa
    return user.membresias.filter(is_active=True, rol='admin').exists()


@rules.predicate
def is_admin_of_empresa(user, empresa):
    """
    Check if the user is an admin of a specific empresa.
    """
    if not user.is_authenticated or not empresa:
        return False
    return user.membresias.filter(
        empresa=empresa,
        is_active=True,
        rol='admin'
    ).exists()


@rules.predicate
def is_member_of_empresa(user, empresa):
    """
    Check if the user is a member of a specific empresa.
    """
    if not user.is_authenticated or not empresa:
        return False
    return user.membresias.filter(
        empresa=empresa,
        is_active=True
    ).exists()


@rules.predicate
def is_superuser(user, obj=None):
    """
    Check if the user is a superuser.
    """
    return user.is_authenticated and user.is_superuser


# =============================================================================
# Dynamic predicate factories
# =============================================================================

def has_role_in_current_empresa(role_name):
    """
    Factory function to create predicates for specific roles.
    
    Usage:
        is_editor = has_role_in_current_empresa('editor')
        rules.add_perm('documents.edit', is_editor)
    """
    @rules.predicate(name=f'has_role:{role_name}')
    def check_role(user, obj=None):
        if not user.is_authenticated:
            return False
        # Check the rol_actual from the request context (set by middleware)
        from django.core.cache import cache
        current_rol = cache.get(f'user_current_rol_{user.id}')
        return current_rol == role_name
    return check_role


# =============================================================================
# Permission rules
# =============================================================================

# Empresa management permissions
rules.add_perm('companies.view_empresa', is_empresa_member)
rules.add_perm('companies.change_empresa', is_empresa_admin | is_superuser)
rules.add_perm('companies.delete_empresa', is_superuser)

# Membresia (team member) permissions
rules.add_perm('companies.view_membresia', is_empresa_member)
rules.add_perm('companies.add_membresia', is_empresa_admin | is_superuser)
rules.add_perm('companies.change_membresia', is_empresa_admin | is_superuser)
rules.add_perm('companies.delete_membresia', is_empresa_admin | is_superuser)
