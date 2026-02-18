import os
from pathlib import Path
from celery import Celery
from celery.schedules import crontab
from dotenv import load_dotenv

# Load .env file before anything else
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('aspeia_finance')

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Load task modules from all registered Django apps.
app.autodiscover_tasks()

# Configuración de tareas periódicas (celery beat)
app.conf.beat_schedule = {
    'verificar-solicitudes-sat-cada-5-min': {
        'task': 'apps.fiscal.tasks.verificar_solicitudes_pendientes',
        'schedule': 300.0,  # cada 5 minutos
    },
    'validar-estado-cfdis-diario': {
        'task': 'apps.fiscal.tasks.validar_estado_cfdis_pendientes',
        'schedule': 86400.0,  # cada 24 horas
        # La tarea internamente decide qué validar según día de semana/mes
    },
    'sincronizar-cfdis-recientes': {
        'task': 'apps.fiscal.tasks.sincronizar_cfdis_recientes',
        'schedule': crontab(minute=0),  # Ejecutar cada hora
        # La tarea validará si ya toca sincronizar según last_sync_at y sync_frequency_hours
    },
    'sincronizacion-semanal-sat-odoo': {
        'task': 'apps.fiscal.tasks.ejecutar_sincronizacion_semanal',
        'schedule': crontab(minute='*/5'),  # Ejecutar cada 5 minutos para coincidir con configuración de usuario
        # La tarea internamente verifica weekly_sync_day, weekly_sync_hour y weekly_sync_minute por empresa
    },
}
app.conf.timezone = 'America/Mexico_City'


@app.task(bind=True)
def debug_task(self):
    print(f'Request: {self.request!r}')
