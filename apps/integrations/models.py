"""
Modelos del módulo de integraciones.
Importa los modelos de los submódulos para que Django los detecte.
"""
from django.db import models

# Importar modelos de Odoo para que Django los detecte
from .odoo.models import OdooConnection, OdooSyncLog

__all__ = ['OdooConnection', 'OdooSyncLog']
