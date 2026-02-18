from django.apps import AppConfig


class FiscalConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.fiscal'
    verbose_name = 'Fiscal'

    def ready(self):
        # Register Celery tasks from fiscal.odoo
        try:
            import apps.fiscal.odoo.tasks  # noqa: F401
        except ImportError:
            pass
