"""
Admin del módulo de integraciones.
Importa las configuraciones de admin de los submódulos.
"""
from django.contrib import admin

# Importar admin de Odoo
from .odoo.admin import OdooConnectionAdmin, OdooSyncLogAdmin
