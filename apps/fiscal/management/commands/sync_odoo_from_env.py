"""
Crea o actualiza la conexión Odoo desde variables de entorno.

Uso (en servidor/Dokploy):
  ODOO_URL=https://mi-odoo.ejemplo.com
  ODOO_DB=mi_db
  ODOO_USERNAME=admin
  ODOO_PASSWORD=secret
  python manage.py sync_odoo_from_env

Opcional:
  ODOO_EMPRESA_ID: ID de la Empresa en Aspeia a vincular (si no se define, se usa la primera activa).
  ODOO_COMPANY_ID: ID de res.company en Odoo; en entornos multiempresa es mejor no usarlo y
    asignar la empresa Odoo desde la app (CFDIs → Configuración → Empresa en Odoo).

En multiempresa: crea una conexión por cada Empresa de Aspeia (ejecutando el comando
con distintos ODOO_EMPRESA_ID) o deja solo URL/DB/user/password en env y asigna la
empresa Odoo desde la interfaz (dropdown "Empresa en Odoo" en CFDIs).
"""
import os

from django.core.management.base import BaseCommand

from apps.companies.models import Empresa
from apps.integrations.odoo.models import OdooConnection


class Command(BaseCommand):
    help = 'Crea o actualiza la conexión Odoo desde variables de entorno (ODOO_*).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Solo mostrar qué se haría, sin guardar.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        url = os.environ.get('ODOO_URL', '').strip()
        db = os.environ.get('ODOO_DB', '').strip()
        username = os.environ.get('ODOO_USERNAME', '').strip()
        password = os.environ.get('ODOO_PASSWORD', '')
        company_id_str = os.environ.get('ODOO_COMPANY_ID', '').strip()
        empresa_id_str = os.environ.get('ODOO_EMPRESA_ID', '').strip()

        if not url or not db or not username or not password:
            self.stdout.write(
                self.style.WARNING(
                    'Faltan variables de entorno. Definir: ODOO_URL, ODOO_DB, ODOO_USERNAME, ODOO_PASSWORD. '
                    'Opcional: ODOO_EMPRESA_ID (ID Empresa en Django). '
                    'En multiempresa, asigna la empresa Odoo desde la app (CFDIs → Empresa en Odoo).'
                )
            )
            return

        # ODOO_COMPANY_ID opcional: en multiempresa se asigna desde la app
        if company_id_str:
            try:
                odoo_company_id = int(company_id_str)
            except ValueError:
                self.stdout.write(self.style.ERROR('ODOO_COMPANY_ID debe ser un número entero.'))
                return
        else:
            odoo_company_id = 1  # placeholder; usuario debe elegir empresa en la app

        # Empresa a la que vincular la conexión
        if empresa_id_str:
            try:
                empresa_id = int(empresa_id_str)
                empresa = Empresa.objects.filter(id=empresa_id, is_active=True).first()
            except ValueError:
                empresa = None
            if not empresa:
                self.stdout.write(
                    self.style.ERROR(f'No se encontró empresa activa con ID={empresa_id_str}.')
                )
                return
        else:
            empresa = Empresa.objects.filter(is_active=True).order_by('id').first()
            if not empresa:
                self.stdout.write(
                    self.style.ERROR('No hay empresas activas. Crea una empresa o indica ODOO_EMPRESA_ID.')
                )
                return

        if dry_run:
            msg = (
                f'[DRY-RUN] Se crearía/actualizaría OdooConnection: empresa={empresa.nombre} (id={empresa.id}), '
                f'url={url}, db={db}, username={username}, odoo_company_id={odoo_company_id}'
            )
            if not company_id_str:
                msg += '. Sin ODOO_COMPANY_ID: asigna la empresa Odoo desde la app (CFDIs).'
            self.stdout.write(msg)
            return

        connection, created = OdooConnection.objects.update_or_create(
            empresa=empresa,
            defaults={
                'odoo_url': url,
                'odoo_db': db,
                'odoo_username': username,
                'odoo_company_id': odoo_company_id,
                'status': 'active',
                'auto_sync_enabled': True,
            },
        )
        connection.set_password(password)
        connection.save()

        if created:
            self.stdout.write(
                self.style.SUCCESS(
                    f'Conexión Odoo creada para empresa "{empresa.nombre}" (id={empresa.id}).'
                )
            )
            if not company_id_str:
                self.stdout.write(
                    self.style.WARNING(
                        'Asigna la empresa Odoo (res.company) desde la app: CFDIs → Configuración → Empresa en Odoo.'
                    )
                )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f'Conexión Odoo actualizada para empresa "{empresa.nombre}" (id={empresa.id}).'
                )
            )
