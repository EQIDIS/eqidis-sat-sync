import os
import requests
import logging
from django.conf import settings
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.utils.decorators import method_decorator
from django.views.generic import TemplateView
from django.views import View
from django.http import JsonResponse

from apps.fiscal.odoo.client import OdooClient, OdooClientError
from apps.companies.models import Empresa
from apps.integrations.odoo.models import OdooConnection
from apps.fiscal.models import CfdiCertificate, EmpresaSyncSettings
from apps.core.encryption import ModelEncryption
from django.utils import timezone
from django.http import HttpResponse
import json
import base64

logger = logging.getLogger(__name__)

# Opcional: Proteger la vista solo para superusuarios o administradores
def is_admin(user):
    return user.is_authenticated and user.is_superuser

@method_decorator(user_passes_test(is_admin), name='dispatch')
class MasterPanelView(TemplateView):
    """
    Panel Maestro (Administrador Global).
    Muestra todas las empresas de Odoo y su estatus de FIEL desde Kidi SAT (DynamoDB).
    Permite configuración masiva y sincronización general.
    """
    template_name = 'fiscal/master_panel.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # 1. Extraer empresas de Odoo (Maestro)
        odoo_data = self.get_odoo_companies()
        
        # 2. Extraer FIELs y passwords desde Node.js (DynamoDB)
        fiel_data = self.get_fiel_credentials()
        
        # 3. Combinar la información (usando odooCompanyId como puente)
        merged_companies = self.merge_data(odoo_data, fiel_data)
        
        # Ordenar: Primero los que tienen FIEL (True antes que False), luego alfabético
        merged_companies.sort(key=lambda x: (not x.get('has_fiel', False), x.get('name', '').lower()))
        
        context['merged_companies'] = merged_companies
        context['total_odoo'] = len(odoo_data)
        context['total_fiels'] = len(fiel_data)
        context['total_ready'] = len([c for c in merged_companies if c.get('has_fiel')])
        
        # Opciones para el formulario de sincronización global
        context['hour_options'] = [{'value': i, 'label': f'{i:02d}:00'} for i in range(24)]
        context['minute_options'] = [{'value': i, 'label': f':{i:02d}'} for i in range(0, 60, 5)]
        context['days_range_options'] = [
            {'value': 7, 'label': '1 semana (7 días)'},
            {'value': 14, 'label': '2 semanas (14 días)'},
            {'value': 21, 'label': '3 semanas (21 días)'},
            {'value': 30, 'label': '1 mes (30 días)'},
            {'value': 60, 'label': '2 meses (60 días)'},
            {'value': 90, 'label': '3 meses (90 días)'},
            {'value': 120, 'label': '4 meses (120 días)'},
            {'value': 150, 'label': '5 meses (150 días)'},
            {'value': 180, 'label': '6 meses (180 días)'},
            {'value': 270, 'label': '9 meses (270 días)'},
            {'value': 365, 'label': '12 meses (365 días)'},
        ]
        
        # Obtener valores predeterminados basados en el primer registro guardado activo (persistencia)
        default_settings = EmpresaSyncSettings.objects.filter(company__is_active=True, weekly_sync_enabled=True).first()
        if default_settings:
            context['global_sync_day'] = default_settings.weekly_sync_day
            context['global_sync_hour'] = default_settings.weekly_sync_hour
            context['global_sync_minute'] = default_settings.weekly_sync_minute
            context['global_sync_range'] = default_settings.weekly_sync_days_range
        else:
            context['global_sync_day'] = 0
            context['global_sync_hour'] = 0
            context['global_sync_minute'] = 0
            context['global_sync_range'] = 30
            
        return context

    def get_odoo_companies(self):
        url = os.environ.get('ODOO_URL')
        db = os.environ.get('ODOO_DB')
        username = os.environ.get('ODOO_USERNAME')
        password = os.environ.get('ODOO_PASSWORD')
        
        if not all([url, db, username, password]):
            logger.warning("Faltan credenciales MAESTRAS de Odoo en el entorno.")
            return []
            
        try:
            client = OdooClient(url, db, username, password)
            client.authenticate()
            # search_read de res.company
            companies = client.search_read(
                'res.company',
                [],
                fields=['id', 'name', 'vat'],
                order='name asc'
            )
            return companies
        except OdooClientError as e:
            logger.error(f"Error cargando empresas de Odoo Maestro: {e}")
            return []

    def get_fiel_credentials(self):
        backend_url = os.environ.get('EQIDIS_BACKEND_URL', 'http://localhost:9000/api/v1')
        token = os.environ.get('INTERNAL_API_TOKEN', '')
        
        if not token:
            logger.warning("Falta INTERNAL_API_TOKEN en el entorno.")
            return []
            
        try:
            # Consumir el endpoint interno de Node.js
            # backend_url suele ser http://localhost:9000/api/v1
            # El endpoint es relative to esto: /internal/fiel-credentials
            clean_url = backend_url.rstrip('/')
            
            response = requests.get(
                f"{clean_url}/internal/fiel-credentials",
                headers={"Authorization": f"Bearer {token}"},
                timeout=45
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"Error consultando el API interno de FIELs: {e}")
            return []

    def merge_data(self, odoo_companies, fiel_credentials):
        """
        Combina la lista de Odoo con la lista de usuarios FIEL (DynamoDB).
        Retorna la lista de Odoo pero enriquecida con estado de FIEL.
        También revisa si la empresa ya existe en Aspeya Finance (modelo `Empresa`).
        """
        # Mapear FIELs por odooCompanyId
        fiel_map = {item['odooCompanyId']: item for item in fiel_credentials if item.get('odooCompanyId')}
        
        # Mapear empresas locales de Aspeya Finance
        local_companies = {emp.rfc: emp for emp in Empresa.objects.all() if emp.rfc}
        
        merged = []
        for odoo_c in odoo_companies:
            odoo_id = odoo_c.get('id')
            rfc = odoo_c.get('vat', '')
            
            fiel_info = fiel_map.get(odoo_id)
            has_fiel = bool(fiel_info and fiel_info.get('fielPass'))
            
            # Ver si ya está registrada en modo tenant en Aspeya Finance
            local_empresa = local_companies.get(rfc)
            
            # Verificar si la sincronización está habilitada (solo si es tenant)
            is_sync_enabled = False
            if local_empresa:
                try:
                    sync_settings = EmpresaSyncSettings.objects.get(company=local_empresa)
                    is_sync_enabled = sync_settings.weekly_sync_enabled
                except EmpresaSyncSettings.DoesNotExist:
                    is_sync_enabled = False
            
            merged.append({
                'odoo_id': odoo_id,
                'name': odoo_c.get('name'),
                'rfc': rfc,
                'has_fiel': has_fiel,
                'fiel_info': fiel_info,  # Datos completos (fiel_cer_path, etc.)
                'is_local_tenant': bool(local_empresa),
                'local_tenant_id': local_empresa.id if local_empresa else None,
                'is_sync_enabled': is_sync_enabled
            })
            
            
        return merged

@method_decorator(user_passes_test(is_admin), name='dispatch')
class MasterPanelSyncView(View):
    """
    Vista que recibe un POST para iniciar o configurar la sincronización de una empresa.
    Si recibe `odoo_id`, provisiona el tenant y configura `EmpresaSyncSettings`.
    """
    def post(self, request, *args, **kwargs):
        try:
            odoo_id = request.POST.get('odoo_id')
            if not odoo_id:
                return HttpResponse('<div class="badge badge-error">Falta ID</div>', status=400)
            odoo_id = int(odoo_id)
            
            # Reutilizamos la lógica del MasterPanelView para obtener los datos
            panel = MasterPanelView()
            odoo_companies = panel.get_odoo_companies()
            fiel_credentials = panel.get_fiel_credentials()
            
            # Encontrar datos de la empresa y la FIEL
            odoo_company = next((c for c in odoo_companies if c['id'] == odoo_id), None)
            fiel_info = next((f for f in fiel_credentials if f.get('odooCompanyId') == odoo_id), None)
            
            if not odoo_company or not fiel_info:
                return HttpResponse('<div class="badge badge-error">Datos no encontrados</div>', status=404)
                
            rfc = odoo_company.get('vat', '')
            name = odoo_company.get('name', '')
            
            # 1. Obtener o crear la Empresa (Tenant) localmente
            empresa, created = Empresa.objects.get_or_create(
                rfc=rfc,
                defaults={'nombre': name}
            )
            
            # 2. Configurar o actualizar el Certificado
            if fiel_info.get('fielPass'):
                now = timezone.now()
                cert = CfdiCertificate.objects.filter(company=empresa, tipo='FIEL').first()
                if not cert:
                    cert = CfdiCertificate(
                        company=empresa,
                        tipo='FIEL',
                        rfc=rfc,
                        status='active',
                        valid_from=now,
                        valid_to=now + timezone.timedelta(days=365*4)  # Placeholder de 4 años
                    )
                else:
                    cert.status = 'active'
                
                # Encriptamos la contraseña desencriptada de Node con Fernet (Django)
                cert.encrypted_password = ModelEncryption.encrypt(fiel_info['fielPass'])
                
                # Sincronizamos físicamente los archivos del bucket de Node (eqidis-certs) al de Django (default_storage)
                try:
                    import boto3
                    from django.core.files.base import ContentFile
                    from django.core.files.storage import default_storage
                    import os
                    
                    # El Node backend guarda en eqidis-certs (o similar). Usaremos boto3 cliente "raw"
                    # para jalar los binarios usando las credenciales del entorno
                    s3_client = boto3.client(
                        's3',
                        aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
                        aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
                        region_name=os.environ.get('AWS_REGION', 'us-east-1')
                    )
                    
                    eqidis_bucket = 'eqidis-certs'
                    eqidis_cer_key = fiel_info.get('fielCerS3Path')
                    eqidis_key_key = fiel_info.get('fielKeyS3Path')
                    
                    if eqidis_cer_key and eqidis_key_key:
                        # Nombres finales en Aspeia
                        cer_filename = os.path.basename(eqidis_cer_key)
                        key_filename = os.path.basename(eqidis_key_key)
                        cer_path_rel = f"company_{empresa.id}/certificados/{cer_filename}"
                        key_path_rel = f"company_{empresa.id}/certificados/{key_filename}"
                        
                        # Bajar a memoria
                        cer_resp = s3_client.get_object(Bucket=eqidis_bucket, Key=eqidis_cer_key)
                        key_resp = s3_client.get_object(Bucket=eqidis_bucket, Key=eqidis_key_key)
                        
                        cer_bytes = cer_resp['Body'].read()
                        key_bytes = key_resp['Body'].read()
                        
                        # Limpiar y guardar en aspeia storage
                        if default_storage.exists(cer_path_rel):
                            default_storage.delete(cer_path_rel)
                        if default_storage.exists(key_path_rel):
                            default_storage.delete(key_path_rel)
                            
                        default_storage.save(cer_path_rel, ContentFile(cer_bytes))
                        default_storage.save(key_path_rel, ContentFile(key_bytes))
                        
                        # Actualizar base de datos con las nuevas rutas nativas
                        cert.s3_cer_path = cer_path_rel
                        cert.s3_key_path = key_path_rel
                    else:
                        raise ValueError("Rutas S3 origen no proporcionadas por el Node API")
                        
                except Exception as e:
                    logger.error(f"Error migrando FIEL desde S3 (bucket eqidis-certs): {e}")
                    # En caso extremo de no poder mover, cae en modo fallback aunque no funcione la validación Celery
                    cert.s3_cer_path = fiel_info.get('fielCerS3Path', '')
                    cert.s3_key_path = fiel_info.get('fielKeyS3Path', '')
                    
                cert.save()
                
                empresa.certificate = cert
                empresa.save()
            
            # 3. Configurar Conexión Odoo local
            url = os.environ.get('ODOO_URL', '')
            db = os.environ.get('ODOO_DB', '')
            username = os.environ.get('ODOO_USERNAME', '')
            password = os.environ.get('ODOO_PASSWORD', '')
            
            conn, conn_created = OdooConnection.objects.get_or_create(
                empresa=empresa,
                defaults={
                    'odoo_url': url,
                    'odoo_db': db,
                    'odoo_username': username,
                    'odoo_company_id': odoo_id,
                    'status': 'active',
                    'auto_sync_enabled': True
                }
            )
            if conn_created or not conn.encrypted_password:
                conn.set_password(password)
                conn.save()
                
            # 4. Configurar Auto-Sync y Sincronización a Odoo
            sync_settings, sync_created = EmpresaSyncSettings.objects.get_or_create(
                company=empresa,
                defaults={
                    'auto_sync_enabled': True,
                    'weekly_sync_enabled': True,
                    'sync_to_odoo_enabled': True,
                    'weekly_sync_days_range': 30
                }
            )
            
            if not sync_created:
                sync_settings.auto_sync_enabled = True
                sync_settings.weekly_sync_enabled = True
                sync_settings.sync_to_odoo_enabled = True
                sync_settings.save()
                
            # Aquí podríamos lanzar la tarea de celery directamente:
            # from apps.fiscal.tasks import ejecutar_sincronizacion_semanal
            # ejecutar_sincronizacion_semanal.delay(empresa.id)
            
            return HttpResponse('<div class="badge badge-success">¡Configurado!</div>')
            
        except Exception as e:
            logger.error(f"Error en MasterPanelSyncView: {e}")
            return HttpResponse(f'<div class="badge badge-error" title="{str(e)}">Error</div>', status=500)

@method_decorator(user_passes_test(is_admin), name='dispatch')
class MasterPanelGlobalSyncConfigView(View):
    """
    Actualiza la configuración de sincronización para TODAS las empresas locales activas.
    """
    def post(self, request, *args, **kwargs):
        try:
            weekly_sync_day = int(request.POST.get('weekly_sync_day', 0))
            weekly_sync_hour = int(request.POST.get('weekly_sync_hour', 0))
            weekly_sync_minute = int(request.POST.get('weekly_sync_minute', 0))
            weekly_sync_days_range = int(request.POST.get('weekly_sync_days_range', 30))
            
            # Obtener todas las configuraciones de empresas activas que TENGAN PRENDIDO el toggle
            settings_to_update = EmpresaSyncSettings.objects.filter(company__is_active=True, weekly_sync_enabled=True)
            
            count = 0
            for setting in settings_to_update:
                setting.weekly_sync_day = weekly_sync_day
                setting.weekly_sync_hour = weekly_sync_hour
                setting.weekly_sync_minute = weekly_sync_minute
                setting.weekly_sync_days_range = weekly_sync_days_range
                setting.save()
                count += 1
                
            return HttpResponse(f'<div class="alert alert-success mt-4"><span>¡Configuración global aplicada a {count} empresas!</span></div>')
            
        except ValueError:
            return HttpResponse('<div class="alert alert-error mt-4"><span>Valores inválidos en la configuración.</span></div>', status=400)
        except Exception as e:
            logger.error(f"Error en MasterPanelGlobalSyncConfigView: {e}")
            return HttpResponse(f'<div class="alert alert-error mt-4"><span>Ocurrió un error: {e}</span></div>', status=500)


@method_decorator(user_passes_test(is_admin), name='dispatch')
class MasterPanelToggleSyncView(View):
    """
    Activa o desactiva la sincronización recurrente para una empresa en particular
    a través del checkbox HTMX en la fila de la tabla.
    """
    def post(self, request, *args, **kwargs):
        try:
            local_tenant_id = request.POST.get('local_tenant_id')
            if not local_tenant_id:
                return HttpResponse(status=400)
            
            # El input type="checkbox" no manda false si no está checkeado,
            # pero como estamos procesando un evento "change", evaluaremos su estado
            is_enabled = request.POST.get('is_enabled') == 'on' or request.POST.get('is_enabled') == 'true'
            
            empresa = Empresa.objects.get(id=local_tenant_id)
            settings, _ = EmpresaSyncSettings.objects.get_or_create(company=empresa)
            settings.weekly_sync_enabled = is_enabled
            
            # Si se está activando, nos aseguramos que también sincronice a Odoo por defecto.
            if is_enabled:
                settings.auto_sync_enabled = True
                settings.sync_to_odoo_enabled = True
                
            settings.save()
            return HttpResponse() # HTTP 200 OK - No content returned needed if not replacing HTML
            
        except Empresa.DoesNotExist:
            return HttpResponse('Empresa no existe', status=404)
        except Exception as e:
            logger.error(f"Error en MasterPanelToggleSyncView: {e}")
            return HttpResponse(status=500)


@method_decorator(user_passes_test(is_admin), name='dispatch')
class MasterPanelSyncNowView(View):
    """
    Encola tareas de descarga en Celery para todas las empresas activas 
    que tengan habilitada la sincronización masiva (weekly_sync_enabled=True).
    """
    def post(self, request, *args, **kwargs):
        from .tasks import solicitar_descarga_sat
        from datetime import date, timedelta
        
        try:
            # Encontrar empresas que TENGAN PRENDIDO el toggle
            settings_to_sync = EmpresaSyncSettings.objects.filter(
                company__is_active=True, 
                weekly_sync_enabled=True
            ).select_related('company')
            
            if not settings_to_sync.exists():
                return HttpResponse(
                    '<div class="alert alert-warning mt-4"><span>No hay empresas seleccionadas para sincronizar.</span></div>',
                    status=400
                )
            
            count = 0
            skipped = 0
            today = date.today()
            
            # Encolar tareas para cada empresa
            for setting in settings_to_sync:
                empresa = setting.company
                # Verificar primero que la FIEL siga siendo válida/activa
                fiel = CfdiCertificate.objects.filter(company=empresa, tipo='FIEL', status='active').first()
                if not fiel:
                    logger.warning(f"Empresa {empresa.nombre} seleccionada pero no tiene FIEL activa. Saltando.")
                    skipped += 1
                    continue
                
                days_range = setting.weekly_sync_days_range if setting.weekly_sync_days_range else 30
                fecha_fin = today - timedelta(days=1)
                fecha_inicio = today - timedelta(days=days_range)
                
                # Encolar RECIBIDOS
                solicitar_descarga_sat.delay(
                    empresa_id=empresa.id,
                    fecha_inicio=fecha_inicio.isoformat(),
                    fecha_fin=fecha_fin.isoformat(),
                    tipo='recibidos',
                    user_id=request.user.id,
                    is_auto_generated=False
                )
                
                # Encolar EMITIDOS
                solicitar_descarga_sat.delay(
                    empresa_id=empresa.id,
                    fecha_inicio=fecha_inicio.isoformat(),
                    fecha_fin=fecha_fin.isoformat(),
                    tipo='emitidos',
                    user_id=request.user.id,
                    is_auto_generated=False
                )
                
                count += 1
                
            msg = f'<div class="alert alert-success mt-4"><span>¡Conectando al SAT! {count} empresas enviadas a la cola de sincronización. (RECIBIDOS y EMITIDOS)</span>'
            if skipped > 0:
                msg += f'<br><span class="opacity-80 text-sm">Nota: se omitieron {skipped} empresa(s) por no tener FIEL configurada o activa en Aspeia. Haz clic en el engrane para configurarlas.</span>'
            msg += '</div>'
            
            return HttpResponse(msg)
            
        except Exception as e:
            logger.error(f"Error en MasterPanelSyncNowView: {e}")
            return HttpResponse(
                f'<div class="alert alert-error mt-4"><span>Ocurrió un error al lanzar sincronización: {e}</span></div>', 
                status=500
            )

@method_decorator(user_passes_test(is_admin), name='dispatch')
class MasterPanelDescargasView(TemplateView):
    """
    Portal Global de Descargas (Administrador Maestro).
    Muestra el historial unificado de descargas SAT para todas las empresas locales.
    """
    template_name = 'fiscal/master_panel_descargas.html'
    
    def get_context_data(self, **kwargs):
        from .models import CfdiDownloadRequest
        context = super().get_context_data(**kwargs)
        
        # Filtramos descargas y las ordenamos por más recientes
        solicitudes = CfdiDownloadRequest.objects.all().select_related('company').prefetch_related('packages').order_by('-created_at')
        
        context['solicitudes'] = solicitudes
        context['total_solicitudes'] = solicitudes.count()
        context['solicitudes_pendientes'] = solicitudes.filter(status__in=['requested', 'ready']).count()
        
        return context

@method_decorator(user_passes_test(is_admin), name='dispatch')
class MasterPanelDescargasEliminarView(View):
    """
    ELIMINACIÓN TOTAL (Super Purge).
    Borra TODO el historial de solicitudes, paquetes, verificaciones de estado, 
    documentos CFDI de la base de datos y FÍSICAMENTE los archivos XML y ZIP 
    del bucket de S3 para todas las empresas que tengan descargas.
    """
    def post(self, request, *args, **kwargs):
        from .models import CfdiDownloadRequest, CfdiDocument, CfdiStateCheck, SatDownloadPackage
        from django.db import transaction
        import boto3
        
        try:
            # 1. Preparar lista de archivos de S3 a borrar
            s3_keys_to_delete = []
            
            # Recopilar paths de XMLs
            xml_paths = CfdiDocument.objects.exclude(s3_xml_path__isnull=True).exclude(s3_xml_path='').values_list('s3_xml_path', flat=True)
            s3_keys_to_delete.extend(list(xml_paths))
            
            # Recopilar paths de ZIPs
            zip_paths = SatDownloadPackage.objects.exclude(s3_zip_path__isnull=True).exclude(s3_zip_path='').values_list('s3_zip_path', flat=True)
            s3_keys_to_delete.extend(list(zip_paths))
            
            # 2. Borrar físicamente de S3 en lotes de 1000
            if s3_keys_to_delete:
                s3_client = boto3.client(
                    's3',
                    aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
                    aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
                    region_name=os.environ.get('AWS_S3_REGION_NAME', 'us-east-1')
                )
                bucket_name = os.environ.get('AWS_STORAGE_BUCKET_NAME')
                
                # DeleteObjects bucket API acepta max 1000 keys por request
                chunk_size = 1000
                for i in range(0, len(s3_keys_to_delete), chunk_size):
                    chunk = s3_keys_to_delete[i:i + chunk_size]
                    delete_payload = {'Objects': [{'Key': key} for key in chunk]}
                    try:
                        s3_client.delete_objects(Bucket=bucket_name, Delete=delete_payload)
                    except Exception as s3_err:
                        logger.error(f"Error borrando lote de S3 durante Super Purge: {s3_err}")
            
            # 3. Borrado en Cascada de la Base de Datos
            with transaction.atomic():
                # Borrar verificaciones de estado (es FK de document)
                CfdiStateCheck.objects.all().delete()
                
                # Borrar documentos CFDI
                cfdis_count, _ = CfdiDocument.objects.all().delete()
                
                # Al borrar solicitudes, los paquetes se borran por cascade
                reqs_count, _ = CfdiDownloadRequest.objects.all().delete()
            
            messages.success(request, f"Purga Global Exitosa: Se eliminaron {reqs_count} historiales SAT, {cfdis_count} XMLs y todos los archivos físicos de Amazon S3.")
            
        except Exception as e:
            logger.error(f"Error crítico en Super Purge del Panel Maestro: {e}")
            messages.error(request, f"Ocurrió un error al intentar purgar el historial: {e}")
            
        return redirect('fiscal:master_panel_descargas')

@method_decorator(user_passes_test(is_admin), name='dispatch')
class MasterPanelDescargaCfdisView(TemplateView):
    """
    Muestra la tabla de CFDIs obtenidos en una Solicitud de Descarga específica,
    reutilizando el diseño visual de fiscal/cfdis.
    """
    template_name = 'fiscal/master_panel_descargas_cfdis.html'
    
    def get_context_data(self, **kwargs):
        from .models import CfdiDownloadRequest, CfdiDocument
        context = super().get_context_data(**kwargs)
        
        request_id = self.kwargs.get('pk')
        solicitud = get_object_or_404(CfdiDownloadRequest, id=request_id)
        
        # Filtramos todos los CFDIs que pertenezcan a algún paquete de esta solicitud
        cfdis = CfdiDocument.objects.filter(
            download_package__request=solicitud
        ).select_related('company', 'current_state_check').order_by('-fecha_emision')
        
        context['solicitud'] = solicitud
        context['cfdis'] = cfdis
        context['total_cfdis'] = cfdis.count()
        return context

