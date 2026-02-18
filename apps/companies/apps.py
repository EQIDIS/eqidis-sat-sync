from django.apps import AppConfig


class CompaniesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.companies'
    verbose_name = 'Empresas'
    
    def ready(self):
        # Import signals when app is ready
        from . import signals  # noqa: F401
        # Import rules for permission registration
        from . import rules  # noqa: F401
