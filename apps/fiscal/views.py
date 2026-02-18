import os
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator
from django.views.generic import TemplateView, View
from django.views.decorators.http import require_POST
from django.http import HttpResponse
from django.contrib import messages
from django.core.files.storage import default_storage

from .forms import CertificadoUploadForm
from .models import CfdiCertificate
from apps.companies.models import Empresa


class TenantMixin:
    """Mixin para verificar tenant activo en vistas fiscales."""
    
    def dispatch(self, request, *args, **kwargs):
        session_key = getattr(settings, 'TENANT_SESSION_KEY', 'active_empresa_id')
        empresa_id = request.session.get(session_key)
        
        if not empresa_id:
            return redirect('companies:seleccionar_empresa')
            
        self.empresa = get_object_or_404(Empresa, id=empresa_id)
        return super().dispatch(request, *args, **kwargs)
    
    def get_upload_dir(self):
        upload_dir = os.path.join(settings.MEDIA_ROOT, 'protected', f'company_{self.empresa.id}', 'certs')
        os.makedirs(upload_dir, exist_ok=True)
        return upload_dir


@method_decorator(login_required, name='dispatch')
class FiscalDashboardView(TenantMixin, TemplateView):
    """Vista principal del dashboard fiscal. Solo lectura + formularios vacíos."""
    template_name = 'fiscal/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['empresa'] = self.empresa
        
        # Certificados activos
        context['fiel'] = CfdiCertificate.objects.filter(
            company=self.empresa, tipo='FIEL', status='active'
        ).order_by('-valid_to').first()
        
        context['csd'] = CfdiCertificate.objects.filter(
            company=self.empresa, tipo='CSD', status='active'
        ).order_by('-valid_to').first()
        
        # Formularios vacíos para modales
        context['form_csd'] = CertificadoUploadForm(prefix='csd', tipo_esperado='CSD')
        context['form_fiel'] = CertificadoUploadForm(prefix='fiel', tipo_esperado='FIEL')
            
        return context


class CertificateUploadMixin(TenantMixin):
    """Mixin común para procesamiento de certificados."""
    tipo_esperado = None
    form_prefix = None
    partial_template = None
    form_context_name = None
    
    def get_upload_path(self, filename):
        """Genera la ruta relativa para almacenamiento (S3 friendly)."""
        tipo_folder = self.tipo_esperado.lower() if self.tipo_esperado else 'other'
        return f"protected/company_{self.empresa.id}/certs/{tipo_folder}/{filename}"
    
    def process_certificate(self, form):
        """Procesa y guarda el certificado. Retorna True en éxito."""
        from django.core.files.storage import default_storage
        
        cert_data = form.cleaned_data['cert_data']
        rfc_cert = cert_data['rfc']
        
        # Validar RFC
        if self.empresa.rfc and self.empresa.rfc != rfc_cert:
            form.add_error(None, f"El RFC del certificado ({rfc_cert}) no coincide con la empresa ({self.empresa.rfc}).")
            return False
        elif not self.empresa.rfc:
            self.empresa.rfc = rfc_cert
            self.empresa.save()
        
        # Guardar archivos usando default_storage (S3)
        cer_filename = f"{rfc_cert}_{cert_data['serial_number']}.cer"
        key_filename = f"{rfc_cert}_{cert_data['serial_number']}.key"
        
        cer_path_rel = self.get_upload_path(cer_filename)
        key_path_rel = self.get_upload_path(key_filename)
        
        # Eliminar si existen (para evitar duplicados o colisiones, aunque S3 suele versionar)
        if default_storage.exists(cer_path_rel):
            default_storage.delete(cer_path_rel)
        if default_storage.exists(key_path_rel):
            default_storage.delete(key_path_rel)
            
        # Guardar
        default_storage.save(cer_path_rel, form.cleaned_data['archivo_cer'])
        default_storage.save(key_path_rel, form.cleaned_data['archivo_key'])
        
        # Desactivar certificados previos
        CfdiCertificate.objects.filter(
            company=self.empresa, tipo=self.tipo_esperado, status='active'
        ).update(status='expired')
        
        # Crear nuevo registro
        certificate = CfdiCertificate.objects.create(
            company=self.empresa,
            rfc=rfc_cert,
            tipo=self.tipo_esperado,
            status='active',
            serial_number=cert_data['serial_number'],
            valid_from=cert_data['valid_from'],
            valid_to=cert_data['valid_to'],
            s3_cer_path=cer_path_rel,
            s3_key_path=key_path_rel,
        )
        certificate.set_password(form.cleaned_data['contrasena'])
        certificate.save()
        
        if self.tipo_esperado == 'CSD':
            self.empresa.certificate = certificate
            self.empresa.save()
        
        return True
    
    def post(self, request, *args, **kwargs):
        form = CertificadoUploadForm(
            request.POST, request.FILES,
            prefix=self.form_prefix,
            tipo_esperado=self.tipo_esperado
        )
        
        if form.is_valid() and self.process_certificate(form):
            # Éxito: enviar headers HTMX para cerrar modal y refrescar
            messages.success(request, f"{self.tipo_esperado} cargado correctamente.")
            response = HttpResponse()
            response['HX-Trigger'] = 'closeModal, refreshPage'
            response['HX-Refresh'] = 'true'
            return response
        
        # Error: retornar partial con errores
        return render(request, self.partial_template, {self.form_context_name: form})


@method_decorator(login_required, name='dispatch')
@method_decorator(require_POST, name='dispatch')
class UploadCSDView(CertificateUploadMixin, View):
    """Endpoint HTMX para subir CSD."""
    tipo_esperado = 'CSD'
    form_prefix = 'csd'
    partial_template = 'fiscal/partials/_form_csd.html'
    form_context_name = 'form_csd'


@method_decorator(login_required, name='dispatch')
@method_decorator(require_POST, name='dispatch')
class UploadFIELView(CertificateUploadMixin, View):
    """Endpoint HTMX para subir FIEL."""
    tipo_esperado = 'FIEL'
    form_prefix = 'fiel'
    partial_template = 'fiscal/partials/_form_fiel.html'
    form_context_name = 'form_fiel'


# =============================================================================
# Vistas para Descarga Masiva SAT
# =============================================================================

@method_decorator(login_required, name='dispatch')
class DescargasListView(TenantMixin, TemplateView):
    """Lista de solicitudes de descarga SAT."""
    template_name = 'fiscal/descargas.html'
    
    def get_context_data(self, **kwargs):
        from .models import CfdiDownloadRequest, CfdiDocument
        
        context = super().get_context_data(**kwargs)
        context['empresa'] = self.empresa
        
        # FIEL activa
        context['fiel'] = CfdiCertificate.objects.filter(
            company=self.empresa, tipo='FIEL', status='active'
        ).first()
        
        # Solicitudes
        solicitudes = CfdiDownloadRequest.objects.filter(
            company=self.empresa
        ).prefetch_related('packages').order_by('-created_at')
        
        context['solicitudes'] = solicitudes
        context['total_solicitudes'] = solicitudes.count()
        context['solicitudes_pendientes'] = solicitudes.filter(
            status__in=['requested', 'ready']
        ).count()
        
        # Total CFDIs descargados
        context['total_cfdis'] = CfdiDocument.objects.filter(
            company=self.empresa,
            source='SAT'
        ).count()
        
        # Settings de Sincronización
        from .models import EmpresaSyncSettings
        settings, _ = EmpresaSyncSettings.objects.get_or_create(company=self.empresa)
        context['sync_settings'] = settings
        
        return context


@method_decorator(login_required, name='dispatch')
@method_decorator(require_POST, name='dispatch')
class DescargasCrearView(TenantMixin, View):
    """Crea una nueva solicitud de descarga al SAT."""
    
    def post(self, request, *args, **kwargs):
        from .tasks import solicitar_descarga_sat
        from .models import CfdiDownloadRequest
        
        fecha_inicio = request.POST.get('fecha_inicio')
        fecha_fin = request.POST.get('fecha_fin')
        tipo = request.POST.get('tipo', 'recibidos')
        
        # Validaciones básicas
        if not fecha_inicio or not fecha_fin:
            messages.error(request, "Debes especificar el rango de fechas.")
            return redirect('fiscal:descargas')
        
        # Verificar FIEL activa
        fiel = CfdiCertificate.objects.filter(
            company=self.empresa, tipo='FIEL', status='active'
        ).first()
        
        if not fiel:
            messages.error(request, "Necesitas configurar tu FIEL antes de descargar CFDIs.")
            return redirect('fiscal:dashboard')
        
        # Advertir si ya existe una solicitud con el mismo rango
        existing = CfdiDownloadRequest.objects.filter(
            company=self.empresa,
            fecha_inicio=fecha_inicio,
            fecha_fin=fecha_fin,
            tipo=tipo,
        ).first()
        
        if existing:
            messages.warning(
                request, 
                f"Ya existe una solicitud para este período ({existing.get_status_display()}). "
                f"Se creará una nueva de todas formas, pero los CFDIs duplicados no se guardarán dos veces."
            )
        
        # Encolar tarea Celery
        solicitar_descarga_sat.delay(
            empresa_id=self.empresa.id,
            fecha_inicio=fecha_inicio,
            fecha_fin=fecha_fin,
            tipo=tipo,
            user_id=request.user.id
        )
        
        messages.success(
            request, 
            f"Solicitud de descarga enviada al SAT. El proceso puede tardar entre 5 y 30 minutos."
        )
        return redirect('fiscal:descargas')


@method_decorator(login_required, name='dispatch')
class DescargasDetalleView(TenantMixin, TemplateView):
    """Detalle de una solicitud de descarga (lista de CFDIs con paginación)."""
    template_name = 'fiscal/descargas_detalle.html'
    
    ALLOWED_PAGE_SIZES = [20, 40, 60, 100]
    DEFAULT_PAGE_SIZE = 20
    
    def get_context_data(self, **kwargs):
        from .models import CfdiDownloadRequest, CfdiDocument
        from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
        
        context = super().get_context_data(**kwargs)
        
        solicitud_id = kwargs.get('pk')
        solicitud = get_object_or_404(
            CfdiDownloadRequest, 
            id=solicitud_id, 
            company=self.empresa
        )
        
        context['solicitud'] = solicitud
        context['empresa'] = self.empresa
        
        # Obtener parámetros de paginación
        page = self.request.GET.get('page', 1)
        page_size = self.request.GET.get('page_size', self.DEFAULT_PAGE_SIZE)
        
        try:
            page_size = int(page_size)
            if page_size not in self.ALLOWED_PAGE_SIZES:
                page_size = self.DEFAULT_PAGE_SIZE
        except (ValueError, TypeError):
            page_size = self.DEFAULT_PAGE_SIZE
        
        # CFDIs de esta solicitud (a través de los paquetes)
        cfdis_qs = CfdiDocument.objects.filter(
            download_package__request=solicitud
        ).order_by('-fecha_emision')
        
        # Total antes de paginar
        context['total_cfdis'] = cfdis_qs.count()
        
        # Paginación
        paginator = Paginator(cfdis_qs, page_size)
        try:
            cfdis = paginator.page(page)
        except PageNotAnInteger:
            cfdis = paginator.page(1)
        except EmptyPage:
            cfdis = paginator.page(paginator.num_pages)
        
        context['cfdis'] = cfdis
        context['page_size'] = page_size
        context['allowed_page_sizes'] = self.ALLOWED_PAGE_SIZES
        
        # Resumen por tipo
        context['ingresos'] = cfdis_qs.filter(tipo_cfdi='I').count()
        context['egresos'] = cfdis_qs.filter(tipo_cfdi='E').count()
        context['pagos'] = cfdis_qs.filter(tipo_cfdi='P').count()
        context['traslados'] = cfdis_qs.filter(tipo_cfdi='T').count()
        
        return context


# =============================================================================
# Vistas para Descarga de Archivos
# =============================================================================

@method_decorator(login_required, name='dispatch')
class DescargarPaqueteView(TenantMixin, View):
    """Descarga un paquete ZIP desde S3."""
    
    def get(self, request, pk, *args, **kwargs):
        from .models import SatDownloadPackage
        from django.http import HttpResponse
        
        package = get_object_or_404(
            SatDownloadPackage, 
            id=pk, 
            request__company=self.empresa
        )
        
        if not package.s3_zip_path:
            messages.error(request, "El paquete aún no tiene archivo disponible.")
            return redirect('fiscal:descargas')
        
        try:
            file_content = default_storage.open(package.s3_zip_path, 'rb').read()
            filename = f"cfdi_package_{package.package_id_sat}.zip"
            
            response = HttpResponse(file_content, content_type='application/zip')
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            response['Content-Length'] = len(file_content)
            return response
        except Exception as e:
            messages.error(request, f"Error al descargar: {e}")
            return redirect('fiscal:descargas')


@method_decorator(login_required, name='dispatch')
class DescargarCfdiView(TenantMixin, View):
    """Descarga un CFDI XML individual desde S3."""
    
    def get(self, request, uuid, *args, **kwargs):
        from .models import CfdiDocument
        from django.http import HttpResponse
        import uuid as uuid_lib
        
        try:
            cfdi_uuid = uuid_lib.UUID(uuid)
        except ValueError:
            messages.error(request, "UUID inválido.")
            return redirect('fiscal:descargas')
        
        cfdi = get_object_or_404(
            CfdiDocument, 
            uuid=cfdi_uuid, 
            company=self.empresa
        )
        
        if not cfdi.s3_xml_path:
            messages.error(request, "El XML no está disponible para descarga.")
            return redirect('fiscal:descargas')
        
        try:
            file_content = default_storage.open(cfdi.s3_xml_path, 'rb').read()
            filename = f"{cfdi.uuid}.xml"
            
            response = HttpResponse(file_content, content_type='application/xml; charset=utf-8')
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            response['Content-Length'] = len(file_content)
            return response
        except Exception as e:
            messages.error(request, f"Error al descargar: {e}")
            return redirect('fiscal:descargas')


# =============================================================================
# Vistas para Cancelaciones Pendientes
# =============================================================================

@method_decorator(login_required, name='dispatch')
class CancelacionesPendientesView(TenantMixin, TemplateView):
    """Lista de CFDIs con cancelación pendiente de aceptar/rechazar."""
    template_name = 'fiscal/cancelaciones_pendientes.html'
    
    def get_context_data(self, **kwargs):
        from .models import CfdiDocument
        from apps.integrations.sat.client import SATClient, SATClientError
        
        context = super().get_context_data(**kwargs)
        context['empresa'] = self.empresa
        
        # Verificar FIEL
        fiel = CfdiCertificate.objects.filter(
            company=self.empresa, tipo='FIEL', status='active'
        ).first()
        
        context['fiel'] = fiel
        context['pendientes'] = []
        context['error'] = None
        
        if not fiel:
            context['error'] = "Necesitas configurar tu FIEL para consultar cancelaciones pendientes."
            return context
        
        # NOTA: satcfdi.pending() lanza NotImplementedError - funcionalidad no disponible aún en la librería
        # Esta funcionalidad se habilitará cuando satcfdi/cfdiclient la implementen
        context['not_implemented'] = True
        context['pendientes'] = []
        context['total_pendientes'] = 0
        
        return context


@method_decorator(login_required, name='dispatch')
class Verificar69BView(TenantMixin, View):
    """Verifica si un RFC está en la Lista Negra 69-B (EFOS)."""
    
    def get(self, request, rfc, *args, **kwargs):
        from apps.integrations.sat.client import SATClient, SATClientError
        from django.http import JsonResponse
        
        fiel = CfdiCertificate.objects.filter(
            company=self.empresa, tipo='FIEL', status='active'
        ).first()
        
        if not fiel:
            return JsonResponse({
                'error': 'FIEL no configurada',
                'is_efos': None,
            })
        
        try:
            client = SATClient(fiel)
            result = client.verificar_lista_69b(rfc)
            return JsonResponse(result)
        except SATClientError as e:
            return JsonResponse({
                'error': str(e),
                'is_efos': None,
            })


@method_decorator(login_required, name='dispatch')
class ValidarEstadoCfdiView(TenantMixin, View):
    """Valida el estado de un CFDI individual on-demand."""
    
    def post(self, request, uuid, *args, **kwargs):
        from .models import CfdiDocument, CfdiStateCheck
        from apps.integrations.sat.client import SATClient, SATClientError
        from django.http import JsonResponse
        from django.utils import timezone
        import uuid as uuid_lib
        
        # Validar UUID
        try:
            cfdi_uuid = uuid_lib.UUID(uuid)
        except ValueError:
            return JsonResponse({'error': 'UUID inválido'}, status=400)
        
        # Obtener CFDI
        cfdi = get_object_or_404(CfdiDocument, uuid=cfdi_uuid, company=self.empresa)
        
        # Obtener FIEL
        fiel = CfdiCertificate.objects.filter(
            company=self.empresa, tipo='FIEL', status='active'
        ).first()
        
        if not fiel:
            return HttpResponse('<span class="badge badge-error badge-xs">Error FIEL</span>', status=400)
        
        try:
            client = SATClient(fiel)
            result = client.validar_estado_cfdi(
                rfc_emisor=cfdi.rfc_emisor,
                rfc_receptor=cfdi.rfc_receptor,
                total=f"{cfdi.total:.2f}",
                uuid=str(cfdi.uuid),
            )
            
            estado_anterior = cfdi.estado_sat
            estado_nuevo = result.get('estado')
            es_cancelable = result.get('es_cancelable')
            es_cambio = estado_anterior != estado_nuevo
            
            # Crear registro de auditoría
            CfdiStateCheck.objects.create(
                document=cfdi,
                certificate=fiel,
                estado_anterior=estado_anterior,
                estado_sat=estado_nuevo or 'Desconocido',
                estado_cancelacion=es_cancelable,
                es_cambio=es_cambio,
                source='uuid_check',
                response_raw=result.get('response_raw'),
            )
            
            # Actualizar documento
            if estado_nuevo:
                cfdi.estado_sat = estado_nuevo
            if es_cancelable:
                cfdi.estado_cancelacion = es_cancelable
            if estado_nuevo == 'Cancelado':
                cfdi.fecha_cancelacion = timezone.now()
            cfdi.save(update_fields=['estado_sat', 'estado_cancelacion', 'fecha_cancelacion'])
            
            # Si es request HTMX, devolver partial template
            if request.headers.get('HX-Request'):
                from django.template.loader import render_to_string
                html = render_to_string('fiscal/partials/cfdi_row.html', {'cfdi': cfdi})
                return HttpResponse(html)
            
            # Fallback JSON for non-HTMX
            from django.http import JsonResponse
            return JsonResponse({
                'success': True,
                'estado': estado_nuevo,
                'es_cancelable': es_cancelable,
                'cambio': es_cambio,
            })
            
        except SATClientError as e:
            return HttpResponse(f'<span class="badge badge-error badge-xs">Error</span>', status=500)


@method_decorator(login_required, name='dispatch')
class ValidarTodosCfdiView(TenantMixin, View):
    """Valida el estado de todos los CFDIs de una solicitud de descarga."""
    
    def post(self, request, pk, *args, **kwargs):
        from .models import CfdiDownloadRequest, CfdiDocument, CfdiStateCheck
        from apps.integrations.sat.client import SATClient, SATClientError
        from django.http import JsonResponse
        from django.utils import timezone
        
        # Obtener solicitud
        solicitud = get_object_or_404(
            CfdiDownloadRequest, 
            id=pk, 
            company=self.empresa
        )
        
        # Obtener FIEL
        fiel = CfdiCertificate.objects.filter(
            company=self.empresa, tipo='FIEL', status='active'
        ).first()
        
        if not fiel:
            return JsonResponse({'error': 'FIEL no configurada'}, status=400)
        
        try:
            client = SATClient(fiel)
            
            # Obtener CFDIs de esta solicitud
            cfdis = CfdiDocument.objects.filter(
                download_package__request=solicitud
            )
            
            validated = 0
            changes = 0
            errors = 0
            
            for cfdi in cfdis:
                try:
                    result = client.validar_estado_cfdi(
                        rfc_emisor=cfdi.rfc_emisor,
                        rfc_receptor=cfdi.rfc_receptor,
                        total=f"{cfdi.total:.2f}",
                        uuid=str(cfdi.uuid),
                    )
                    
                    estado_anterior = cfdi.estado_sat
                    estado_nuevo = result.get('estado')
                    es_cancelable = result.get('es_cancelable')
                    es_cambio = estado_anterior != estado_nuevo
                    
                    # Crear registro de auditoría
                    CfdiStateCheck.objects.create(
                        document=cfdi,
                        certificate=fiel,
                        estado_anterior=estado_anterior,
                        estado_sat=estado_nuevo or 'Desconocido',
                        estado_cancelacion=es_cancelable,
                        es_cambio=es_cambio,
                        source='uuid_check',
                        response_raw=result.get('response_raw'),
                    )
                    
                    # Actualizar documento
                    if estado_nuevo:
                        cfdi.estado_sat = estado_nuevo
                    if es_cancelable:
                        cfdi.estado_cancelacion = es_cancelable
                    if estado_nuevo == 'Cancelado':
                        cfdi.fecha_cancelacion = timezone.now()
                    cfdi.save(update_fields=['estado_sat', 'estado_cancelacion', 'fecha_cancelacion'])
                    
                    validated += 1
                    if es_cambio:
                        changes += 1
                        
                except SATClientError:
                    errors += 1
                except Exception:
                    errors += 1
            
            # Si es request HTMX, devolver partial template con todas las filas actualizadas
            if request.headers.get('HX-Request'):
                from django.template.loader import render_to_string
                # Refetch CFDIs para obtener datos actualizados
                cfdis_updated = CfdiDocument.objects.filter(
                    download_package__request=solicitud
                ).order_by('-fecha_emision')
                html = render_to_string('fiscal/partials/cfdi_table_rows.html', {'cfdis': cfdis_updated})
                return HttpResponse(html)
            
            # Fallback JSON for non-HTMX
            return JsonResponse({
                'success': True,
                'validated': validated,
                'changes': changes,
                'errors': errors,
                'total': cfdis.count(),
            })
            
        except Exception as e:
            if request.headers.get('HX-Request'):
                return HttpResponse('<tr><td colspan="10" class="text-error">Error al validar</td></tr>', status=500)
            return JsonResponse({'error': str(e)}, status=500)

@method_decorator(login_required, name='dispatch')
class UpdateSyncSettingsView(TenantMixin, View):
    """
    Actualiza la configuración de sincronización automática (HTMX).
    """
    def post(self, request, *args, **kwargs):
        from .models import EmpresaSyncSettings
        import json
        
        empresa = self.empresa
        auto_sync = request.POST.get('auto_sync') == 'on'
        scheduled_hour = request.POST.get('scheduled_hour')
        
        # Validar hora
        try:
            scheduled_hour = int(scheduled_hour)
            if not (0 <= scheduled_hour <= 23):
                raise ValueError
        except (ValueError, TypeError):
            scheduled_hour = 3  # Default fallback
            
        # Get or Create settings
        settings, created = EmpresaSyncSettings.objects.get_or_create(
            company=empresa
        )
        
        settings.auto_sync_enabled = auto_sync
        settings.scheduled_start_hour = scheduled_hour
        settings.save()
        
        # Respuesta HTMX con trigger para Toast
        response = HttpResponse(status=204)
        response['HX-Trigger'] = json.dumps({
            'showToast': {
                'message': 'Configuración actualizada correctamente',
                'type': 'success'
            }
        })
        return response


# =============================================================================
# Configuración semanal y logs (usados desde fiscal/cfdis)
# =============================================================================

@method_decorator(login_required, name='dispatch')
@method_decorator(require_POST, name='dispatch')
class UpdateWeeklySyncSettingsView(TenantMixin, View):
    """Actualiza configuración de sincronización semanal (HTMX)."""
    
    def post(self, request, *args, **kwargs):
        from .models import EmpresaSyncSettings
        import json
        
        # Get or Create settings
        settings, _ = EmpresaSyncSettings.objects.get_or_create(company=self.empresa)
        
        # Actualizar campos
        settings.weekly_sync_enabled = request.POST.get('weekly_sync_enabled') == 'on'
        settings.sync_to_odoo_enabled = request.POST.get('sync_to_odoo_enabled') == 'on'
        
        try:
            settings.weekly_sync_day = int(request.POST.get('weekly_sync_day', 0))
        except (ValueError, TypeError):
            settings.weekly_sync_day = 0
        
        try:
            settings.weekly_sync_hour = int(request.POST.get('weekly_sync_hour', 4))
        except (ValueError, TypeError):
            settings.weekly_sync_hour = 4
        
        try:
            settings.weekly_sync_minute = int(request.POST.get('weekly_sync_minute', 0))
        except (ValueError, TypeError):
            settings.weekly_sync_minute = 0
        
        try:
            settings.weekly_sync_days_range = int(request.POST.get('weekly_sync_days_range', 7))
        except (ValueError, TypeError):
            settings.weekly_sync_days_range = 7
        
        settings.save()
        
        # Respuesta HTMX
        response = HttpResponse(status=204)
        response['HX-Trigger'] = json.dumps({
            'showToast': {
                'message': 'Configuración semanal actualizada',
                'type': 'success'
            }
        })
        return response


@method_decorator(login_required, name='dispatch')
class SyncSemanalLogsView(TenantMixin, View):
    """Retorna logs de sincronización (HTMX partial)."""
    
    def get(self, request, *args, **kwargs):
        from .models import CfdiDownloadRequest
        from apps.integrations.odoo.models import OdooSyncLog, OdooConnection
        
        tipo = request.GET.get('tipo', 'descargas')
        
        if tipo == 'odoo':
            # Logs de Odoo
            connection = OdooConnection.objects.filter(empresa=self.empresa).first()
            if not connection:
                return HttpResponse('<div class="text-center py-8 text-base-content/50">Sin conexión Odoo configurada</div>')
            
            logs = OdooSyncLog.objects.filter(connection=connection).order_by('-created_at')[:20]
            
            rows = []
            for log in logs:
                status_badge = {
                    'success': '<span class="badge badge-success badge-sm">✓</span>',
                    'error': '<span class="badge badge-error badge-sm">✗</span>',
                    'pending': '<span class="loading loading-spinner loading-xs"></span>',
                }.get(log.status, '<span class="badge badge-ghost badge-sm">?</span>')
                
                rows.append(f'''
                    <tr>
                        <td class="font-mono text-sm">{log.created_at.strftime('%d/%m %H:%M')}</td>
                        <td class="font-mono text-xs">{str(log.cfdi_uuid)[:8]}...</td>
                        <td>{status_badge}</td>
                        <td class="text-sm">{log.action_taken or '-'}</td>
                    </tr>
                ''')
            
            if not rows:
                return HttpResponse('<div class="text-center py-8 text-base-content/50">No hay sincronizaciones Odoo recientes</div>')
            
            return HttpResponse(f'''
                <table class="table table-zebra table-sm">
                    <thead>
                        <tr>
                            <th>Fecha</th>
                            <th>UUID</th>
                            <th>Estado</th>
                            <th>Acción</th>
                        </tr>
                    </thead>
                    <tbody>
                        {''.join(rows)}
                    </tbody>
                </table>
            ''')
        
        else:
            # Logs de descargas SAT
            solicitudes = CfdiDownloadRequest.objects.filter(
                company=self.empresa
            ).order_by('-created_at')[:10]
            
            rows = []
            for s in solicitudes:
                tipo_badge = '<span class="badge badge-info badge-sm">Emitidos</span>' if s.tipo == 'emitidos' else '<span class="badge badge-success badge-sm">Recibidos</span>'
                status_badge = {
                    'downloaded': '<span class="badge badge-success badge-sm">✓</span>',
                    'failed': '<span class="badge badge-error badge-sm">✗</span>',
                }.get(s.status, '<span class="loading loading-spinner loading-xs"></span>')
                origen = '<span class="badge badge-ghost badge-sm">Auto</span>' if s.is_auto_generated else '<span class="badge badge-outline badge-sm">Manual</span>'
                
                rows.append(f'''
                    <tr>
                        <td class="font-mono text-sm">{s.created_at.strftime('%d/%m %H:%M')}</td>
                        <td>{tipo_badge}</td>
                        <td class="text-sm">{s.fecha_inicio.strftime('%d/%m')} - {s.fecha_fin.strftime('%d/%m')}</td>
                        <td>{status_badge}</td>
                        <td class="font-mono">{getattr(s, 'total_cfdis', '-') or '-'}</td>
                        <td>{origen}</td>
                    </tr>
                ''')
            
            if not rows:
                return HttpResponse('<div class="text-center py-8 text-base-content/50">No hay descargas recientes</div>')
            
            return HttpResponse(f'''
                <table class="table table-zebra table-sm">
                    <thead>
                        <tr>
                            <th>Fecha</th>
                            <th>Tipo</th>
                            <th>Rango</th>
                            <th>Estado</th>
                            <th>CFDIs</th>
                            <th>Origen</th>
                        </tr>
                    </thead>
                    <tbody>
                        {''.join(rows)}
                    </tbody>
                </table>
            ''')


# =============================================================================
# Vista Unificada de CFDIs
# =============================================================================

@method_decorator(login_required, name='dispatch')
class CfdiListView(TenantMixin, TemplateView):
    """Vista unificada de todos los CFDIs de la empresa con sync integrado."""
    template_name = 'fiscal/cfdis.html'

    ALLOWED_PAGE_SIZES = [25, 50, 100]
    DEFAULT_PAGE_SIZE = 50

    def get_context_data(self, **kwargs):
        from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
        from django.db.models import Q, Sum
        from datetime import date, timedelta
        from .models import CfdiDocument, EmpresaSyncSettings, CfdiDownloadRequest
        from apps.integrations.odoo.models import OdooConnection

        context = super().get_context_data(**kwargs)
        context['empresa'] = self.empresa

        # =====================================================================
        # Configuración de Sync (integrado de sync_semanal)
        # =====================================================================

        # FIEL activa
        context['fiel'] = CfdiCertificate.objects.filter(
            company=self.empresa, tipo='FIEL', status='active'
        ).first()

        # Conexión Odoo (cualquier estado para mostrar selector de empresa)
        try:
            context['odoo_connection'] = OdooConnection.objects.filter(
                empresa=self.empresa
            ).first()
            # Permitir configurar empresa Odoo si hay conexión o env vars (multiempresa)
            import os
            has_env = all([
                os.environ.get('ODOO_URL', '').strip(),
                os.environ.get('ODOO_DB', '').strip(),
                os.environ.get('ODOO_USERNAME', '').strip(),
                os.environ.get('ODOO_PASSWORD', ''),
            ])
            context['odoo_can_configure'] = bool(context['odoo_connection']) or has_env
        except Exception:
            context['odoo_connection'] = None
            context['odoo_can_configure'] = False

        # Settings de Sincronización
        sync_settings, _ = EmpresaSyncSettings.objects.get_or_create(company=self.empresa)
        context['sync_settings'] = sync_settings

        # Calcular rango de fechas según configuración
        today = date.today()
        days_range = sync_settings.weekly_sync_days_range or 7
        fecha_fin = today - timedelta(days=1)
        fecha_inicio = today - timedelta(days=days_range)
        context['sync_fecha_inicio'] = fecha_inicio
        context['sync_fecha_fin'] = fecha_fin
        context['days_range'] = days_range

        # Opciones de rango de días (hasta 12 meses)
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

        # Próximo día de ejecución
        days = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']
        context['next_execution_day'] = days[sync_settings.weekly_sync_day]

        # Opciones de hora para el selector
        context['hour_options'] = [{'value': i, 'label': f'{i:02d}:00'} for i in range(24)]

        # Opciones de minuto para el selector
        context['minute_options'] = [{'value': i, 'label': f':{i:02d}'} for i in range(0, 60, 5)]

        # Solicitudes recientes (últimas 5)
        context['solicitudes_recientes'] = CfdiDownloadRequest.objects.filter(
            company=self.empresa
        ).order_by('-created_at')[:5]

        # =====================================================================
        # Filtros y Tabla de CFDIs
        # =====================================================================

        # Obtener filtros
        tipo = self.request.GET.get('tipo', '')
        estado_sat = self.request.GET.get('estado_sat', '')
        cfdi_state = self.request.GET.get('cfdi_state', '')
        rfc = self.request.GET.get('rfc', '')
        fecha_desde = self.request.GET.get('fecha_desde', '')
        fecha_hasta = self.request.GET.get('fecha_hasta', '')

        # Query base
        cfdis = CfdiDocument.objects.filter(company=self.empresa)

        # Aplicar filtros
        if tipo:
            cfdis = cfdis.filter(tipo_cfdi=tipo)
        if estado_sat:
            cfdis = cfdis.filter(estado_sat=estado_sat)
        if cfdi_state:
            cfdis = cfdis.filter(cfdi_state=cfdi_state)
        if rfc:
            cfdis = cfdis.filter(
                Q(rfc_emisor__icontains=rfc) |
                Q(rfc_receptor__icontains=rfc) |
                Q(nombre_emisor__icontains=rfc) |
                Q(nombre_receptor__icontains=rfc)
            )
        if fecha_desde:
            cfdis = cfdis.filter(fecha_emision__gte=fecha_desde)
        if fecha_hasta:
            cfdis = cfdis.filter(fecha_emision__lte=fecha_hasta + ' 23:59:59')

        # Ordenamiento
        cfdis = cfdis.order_by('-fecha_emision')

        # Paginación
        page = self.request.GET.get('page', 1)
        page_size = self.request.GET.get('page_size', self.DEFAULT_PAGE_SIZE)

        try:
            page_size = int(page_size)
            if page_size not in self.ALLOWED_PAGE_SIZES:
                page_size = self.DEFAULT_PAGE_SIZE
        except (ValueError, TypeError):
            page_size = self.DEFAULT_PAGE_SIZE

        paginator = Paginator(cfdis, page_size)

        try:
            cfdis_page = paginator.page(page)
        except PageNotAnInteger:
            cfdis_page = paginator.page(1)
        except EmptyPage:
            cfdis_page = paginator.page(paginator.num_pages)

        context['cfdis'] = cfdis_page
        context['page_size'] = page_size
        context['allowed_page_sizes'] = self.ALLOWED_PAGE_SIZES

        # Estadísticas generales (sin filtros)
        all_cfdis = CfdiDocument.objects.filter(company=self.empresa)
        context['stats'] = {
            'total': all_cfdis.count(),
            'vigentes': all_cfdis.filter(estado_sat='Vigente').count(),
            'cancelados': all_cfdis.filter(estado_sat='Cancelado').count(),
            'ingresos': all_cfdis.filter(tipo_cfdi='I').count(),
            'egresos': all_cfdis.filter(tipo_cfdi='E').count(),
            'pagos': all_cfdis.filter(tipo_cfdi='P').count(),
            'traslados': all_cfdis.filter(tipo_cfdi='T').count(),
            'total_monto': all_cfdis.filter(estado_sat='Vigente').aggregate(
                total=Sum('total')
            )['total'] or 0,
        }

        # Filtros actuales (para mantener estado en paginación)
        context['filtros'] = {
            'tipo': tipo,
            'estado_sat': estado_sat,
            'cfdi_state': cfdi_state,
            'rfc': rfc,
            'fecha_desde': fecha_desde,
            'fecha_hasta': fecha_hasta,
        }

        # Opciones para filtros
        context['tipo_choices'] = CfdiDocument.TIPO_CHOICES
        context['estado_sat_choices'] = CfdiDocument.ESTADO_SAT_CHOICES
        context['cfdi_state_choices'] = CfdiDocument.CFDI_STATE_CHOICES

        return context


@method_decorator(login_required, name='dispatch')
class CfdiDetalleView(TenantMixin, TemplateView):
    """Vista de detalle de un CFDI individual."""
    template_name = 'fiscal/cfdi_detalle.html'

    def get_context_data(self, **kwargs):
        from .models import CfdiDocument, CfdiStateCheck
        import uuid as uuid_lib

        context = super().get_context_data(**kwargs)
        context['empresa'] = self.empresa

        uuid_str = kwargs.get('uuid')
        try:
            cfdi_uuid = uuid_lib.UUID(uuid_str)
        except (ValueError, TypeError):
            context['error'] = 'UUID inválido'
            return context

        cfdi = get_object_or_404(
            CfdiDocument,
            uuid=cfdi_uuid,
            company=self.empresa
        )
        context['cfdi'] = cfdi

        # Historial de verificaciones
        context['state_checks'] = CfdiStateCheck.objects.filter(
            document=cfdi
        ).order_by('-checked_at')[:10]

        return context


@method_decorator(login_required, name='dispatch')
class CfdiTablePartialView(TenantMixin, View):
    """Vista parcial HTMX para la tabla de CFDIs."""

    def get(self, request, *args, **kwargs):
        from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
        from django.db.models import Q
        from django.template.loader import render_to_string
        from .models import CfdiDocument

        # Obtener filtros
        tipo = request.GET.get('tipo', '')
        estado_sat = request.GET.get('estado_sat', '')
        cfdi_state = request.GET.get('cfdi_state', '')
        rfc = request.GET.get('rfc', '')
        fecha_desde = request.GET.get('fecha_desde', '')
        fecha_hasta = request.GET.get('fecha_hasta', '')

        # Query base
        cfdis = CfdiDocument.objects.filter(company=self.empresa)

        # Aplicar filtros
        if tipo:
            cfdis = cfdis.filter(tipo_cfdi=tipo)
        if estado_sat:
            cfdis = cfdis.filter(estado_sat=estado_sat)
        if cfdi_state:
            cfdis = cfdis.filter(cfdi_state=cfdi_state)
        if rfc:
            cfdis = cfdis.filter(
                Q(rfc_emisor__icontains=rfc) |
                Q(rfc_receptor__icontains=rfc) |
                Q(nombre_emisor__icontains=rfc) |
                Q(nombre_receptor__icontains=rfc)
            )
        if fecha_desde:
            cfdis = cfdis.filter(fecha_emision__gte=fecha_desde)
        if fecha_hasta:
            cfdis = cfdis.filter(fecha_emision__lte=fecha_hasta + ' 23:59:59')

        # Ordenamiento
        cfdis = cfdis.order_by('-fecha_emision')

        # Paginación
        page = request.GET.get('page', 1)
        page_size = int(request.GET.get('page_size', 50))
        paginator = Paginator(cfdis, page_size)

        try:
            cfdis_page = paginator.page(page)
        except PageNotAnInteger:
            cfdis_page = paginator.page(1)
        except EmptyPage:
            cfdis_page = paginator.page(paginator.num_pages)

        html = render_to_string('fiscal/partials/_cfdi_table.html', {
            'cfdis': cfdis_page,
            'filtros': {
                'tipo': tipo,
                'estado_sat': estado_sat,
                'cfdi_state': cfdi_state,
                'rfc': rfc,
                'fecha_desde': fecha_desde,
                'fecha_hasta': fecha_hasta,
            },
            'page_size': page_size,
        }, request=request)

        return HttpResponse(html)


@method_decorator(login_required, name='dispatch')
@method_decorator(require_POST, name='dispatch')
class ResetCfdisView(TenantMixin, View):
    """
    Borra todos los CFDIs y el historial de descargas de la empresa.
    NO borra la configuración de sincronización.
    """

    def post(self, request, *args, **kwargs):
        from .models import CfdiDownloadRequest, CfdiDocument, CfdiStateCheck
        from django.db import transaction
        import json

        # Confirmar que el usuario quiere borrar
        confirmar = request.POST.get('confirmar') == 'on'
        if not confirmar:
            return HttpResponse('''
                <div class="alert alert-warning mb-4">
                    <span>Debes confirmar que deseas borrar todos los datos.</span>
                </div>
            ''')

        try:
            with transaction.atomic():
                # Contar antes de borrar
                cfdis_count = CfdiDocument.objects.filter(company=self.empresa).count()
                solicitudes_count = CfdiDownloadRequest.objects.filter(company=self.empresa).count()
                checks_count = CfdiStateCheck.objects.filter(document__company=self.empresa).count()

                # Borrar verificaciones de estado (dependen de CFDIs)
                CfdiStateCheck.objects.filter(document__company=self.empresa).delete()

                # Borrar CFDIs
                CfdiDocument.objects.filter(company=self.empresa).delete()

                # Borrar solicitudes de descarga (los paquetes se borran en cascada)
                CfdiDownloadRequest.objects.filter(company=self.empresa).delete()
                
                # Borrar logs de Odoo y resetear fecha de sync
                from apps.integrations.odoo.models import OdooSyncLog, OdooConnection
                OdooSyncLog.objects.filter(connection__empresa=self.empresa).delete()
                OdooConnection.objects.filter(empresa=self.empresa).update(last_sync=None)
                
            # Respuesta HTMX
            response = HttpResponse(f'''
                <div class="alert alert-success mb-4">
                    <svg xmlns="http://www.w3.org/2000/svg" class="stroke-current shrink-0 h-6 w-6" fill="none" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
                    <div>
                        <span class="font-bold">Datos eliminados correctamente</span>
                        <p class="text-sm">Se eliminaron {cfdis_count} CFDIs, historial SAT y logs de Odoo.</p>
                    </div>
                </div>
            ''')
            response['HX-Trigger'] = json.dumps({
                'showToast': {
                    'message': f'Se eliminaron {cfdis_count} CFDIs y logs asociados',
                    'type': 'success'
                },
                'refreshStats': True
            })
            return response

        except Exception as e:
            return HttpResponse(f'''
                <div class="alert alert-error mb-4">
                    <span>Error al borrar: {str(e)}</span>
                </div>
            ''')


@method_decorator(login_required, name='dispatch')
@method_decorator(require_POST, name='dispatch')
class EjecutarSyncManualView(TenantMixin, View):
    """Ejecuta sincronización manual (descarga SAT)."""

    def post(self, request, *args, **kwargs):
        from .tasks import solicitar_descarga_sat
        from .models import EmpresaSyncSettings
        from datetime import date, timedelta

        # Obtener configuración de días
        sync_settings = EmpresaSyncSettings.objects.filter(company=self.empresa).first()
        days_range = sync_settings.weekly_sync_days_range if sync_settings else 7

        # Calcular rango de fechas
        today = date.today()
        fecha_fin = today - timedelta(days=1)
        fecha_inicio = today - timedelta(days=days_range)

        # Verificar FIEL
        fiel = CfdiCertificate.objects.filter(
            company=self.empresa, tipo='FIEL', status='active'
        ).first()

        if not fiel:
            return HttpResponse('''
                <div class="alert alert-error mb-4">
                    <span>FIEL no configurada. Configura tu e.firma antes de continuar.</span>
                </div>
            ''')

        # Encolar descarga RECIBIDOS
        solicitar_descarga_sat.delay(
            empresa_id=self.empresa.id,
            fecha_inicio=fecha_inicio.isoformat(),
            fecha_fin=fecha_fin.isoformat(),
            tipo='recibidos',
            user_id=request.user.id,
            is_auto_generated=False
        )

        # Encolar descarga EMITIDOS
        solicitar_descarga_sat.delay(
            empresa_id=self.empresa.id,
            fecha_inicio=fecha_inicio.isoformat(),
            fecha_fin=fecha_fin.isoformat(),
            tipo='emitidos',
            user_id=request.user.id,
            is_auto_generated=False
        )

        # Mensaje sobre Odoo
        odoo_msg = ""
        if sync_settings and sync_settings.sync_to_odoo_enabled:
            odoo_msg = " La sincronización a Odoo se ejecutará automáticamente al completar."

        return HttpResponse(f'''
            <div class="alert alert-success mb-4">
                <svg xmlns="http://www.w3.org/2000/svg" class="stroke-current shrink-0 h-6 w-6" fill="none" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
                <div>
                    <span class="font-bold">Sincronización iniciada</span>
                    <p class="text-sm">
                        Descargando CFDIs del {fecha_inicio.strftime('%d/%m/%Y')} al {fecha_fin.strftime('%d/%m/%Y')} ({days_range} días).{odoo_msg}
                    </p>
                </div>
            </div>
        ''')


# =============================================================================
# Vistas Odoo (integración centralizada en fiscal/cfdis)
# =============================================================================

@method_decorator(login_required, name='dispatch')
@method_decorator(require_POST, name='dispatch')
class OdooTestConnectionView(TenantMixin, View):
    """Prueba la conexión con Odoo (HTMX)."""

    def post(self, request, *args, **kwargs):
        from apps.integrations.odoo.models import OdooConnection
        from apps.fiscal.odoo.client import create_client_from_connection, OdooClientError
        import json

        connection = OdooConnection.objects.filter(
            empresa=self.empresa
        ).first()

        if not connection:
            response = HttpResponse('<div class="alert alert-warning">Conexión Odoo no configurada</div>')
            response['HX-Trigger'] = json.dumps({
                'showToast': {'message': 'Configura la conexión Odoo desde el Admin', 'type': 'warning'}
            })
            return response

        try:
            client = create_client_from_connection(connection)
            version = client.get_version()
            connection.status = 'active'
            connection.last_error = None
            connection.save()
            messages.success(request, f'Conexión exitosa a Odoo {version.get("server_version", "")}')
        except OdooClientError as e:
            connection.status = 'error'
            connection.last_error = str(e)
            connection.save()
            messages.error(request, f'Error de conexión: {e}')

        response = HttpResponse()
        response['HX-Refresh'] = 'true'
        return response


def _get_odoo_client_for_empresa(empresa):
    """
    Obtiene un cliente Odoo para la empresa: usa su OdooConnection si existe,
    o credenciales de env (ODOO_URL, ODOO_DB, ODOO_USERNAME, ODOO_PASSWORD) para listar empresas.
    Returns (client, connection | None). connection es None si se usó env.
    """
    import os
    from apps.integrations.odoo.models import OdooConnection
    from apps.fiscal.odoo.client import OdooClient, create_client_from_connection

    connection = OdooConnection.objects.filter(empresa=empresa).first()
    if connection:
        try:
            client = create_client_from_connection(connection)
            return client, connection
        except Exception:
            pass
    url = os.environ.get('ODOO_URL', '').strip()
    db = os.environ.get('ODOO_DB', '').strip()
    username = os.environ.get('ODOO_USERNAME', '').strip()
    password = os.environ.get('ODOO_PASSWORD', '')
    if url and db and username and password:
        try:
            client = OdooClient(url=url, db=db, username=username, password=password)
            client.authenticate()
            return client, None
        except Exception:
            pass
    return None, None


@method_decorator(login_required, name='dispatch')
class OdooCompaniesJsonView(TenantMixin, View):
    """Lista empresas (res.company) de Odoo para selección multiempresa. GET → JSON."""

    def get(self, request, *args, **kwargs):
        from django.http import JsonResponse
        from apps.fiscal.odoo.client import OdooClientError

        client, _ = _get_odoo_client_for_empresa(self.empresa)
        if not client:
            return JsonResponse(
                {'error': 'Sin conexión Odoo. Configura en Admin o define ODOO_URL, ODOO_DB, ODOO_USERNAME, ODOO_PASSWORD en env.'},
                status=400
            )
        try:
            companies = client.get_companies()
            return JsonResponse({'companies': companies})
        except OdooClientError as e:
            return JsonResponse({'error': str(e)}, status=502)


@method_decorator(login_required, name='dispatch')
class OdooCompaniesOptionsView(TenantMixin, View):
    """Devuelve fragmento HTML de <option> para el select de empresas Odoo (multiempresa). GET."""

    def get(self, request, *args, **kwargs):
        from apps.fiscal.odoo.client import OdooClientError

        from apps.integrations.odoo.models import OdooConnection

        client, _ = _get_odoo_client_for_empresa(self.empresa)
        if not client:
            return HttpResponse(
                '<option value="">Sin conexión Odoo (configura en Admin o env ODOO_*)</option>'
            )
        selected_id = None
        conn = OdooConnection.objects.filter(empresa=self.empresa).first()
        if conn:
            selected_id = conn.odoo_company_id
        try:
            companies = client.get_companies()
        except OdooClientError:
            return HttpResponse('<option value="">Error al cargar empresas</option>')
        options = ['<option value="">— Selecciona empresa en Odoo —</option>']
        for c in companies:
            sid = c.get('id')
            name = (c.get('name') or '').replace('<', '&lt;').replace('>', '&gt;')
            sel = ' selected' if sid == selected_id else ''
            options.append(f'<option value="{sid}"{sel}>{name} (ID {sid})</option>')
        return HttpResponse(''.join(options))


@method_decorator(login_required, name='dispatch')
@method_decorator(require_POST, name='dispatch')
class OdooSetCompanyView(TenantMixin, View):
    """Asigna la empresa de Odoo (res.company) para la empresa actual. POST odoo_company_id."""

    def post(self, request, *args, **kwargs):
        import os
        import json
        from django.http import JsonResponse
        from apps.integrations.odoo.models import OdooConnection

        try:
            odoo_company_id = int(request.POST.get('odoo_company_id', 0))
        except (ValueError, TypeError):
            return JsonResponse({'error': 'odoo_company_id inválido'}, status=400)

        if odoo_company_id <= 0:
            return JsonResponse({'error': 'Selecciona una empresa de Odoo'}, status=400)

        connection = OdooConnection.objects.filter(empresa=self.empresa).first()
        if connection:
            connection.odoo_company_id = odoo_company_id
            connection.save()
            if request.headers.get('HX-Request'):
                resp = HttpResponse(status=204)
                resp['HX-Trigger'] = json.dumps({
                    'showToast': {'message': 'Empresa Odoo actualizada', 'type': 'success'},
                    'odooCompanyUpdated': True,
                })
                return resp
            return JsonResponse({'success': True, 'message': 'Empresa Odoo actualizada'})

        # Crear conexión desde env si no existe
        url = os.environ.get('ODOO_URL', '').strip()
        db = os.environ.get('ODOO_DB', '').strip()
        username = os.environ.get('ODOO_USERNAME', '').strip()
        password = os.environ.get('ODOO_PASSWORD', '')
        if not (url and db and username and password):
            return JsonResponse(
                {'error': 'Sin conexión guardada y sin ODOO_* en env. Crea la conexión en Admin o define variables.'},
                status=400
            )
        connection = OdooConnection.objects.create(
            empresa=self.empresa,
            odoo_url=url,
            odoo_db=db,
            odoo_username=username,
            odoo_company_id=odoo_company_id,
            status='active',
            auto_sync_enabled=True,
        )
        connection.set_password(password)
        connection.save()
        if request.headers.get('HX-Request'):
            resp = HttpResponse(status=204)
            resp['HX-Trigger'] = json.dumps({
                'showToast': {'message': 'Conexión Odoo creada y empresa asignada', 'type': 'success'},
                'odooCompanyUpdated': True,
            })
            return resp
        return JsonResponse({'success': True, 'message': 'Conexión creada y empresa asignada'})


@method_decorator(login_required, name='dispatch')
@method_decorator(require_POST, name='dispatch')
class OdooImportView(TenantMixin, View):
    """Importa CFDIs desde Odoo hacia Aspeia (HTMX)."""

    def post(self, request, *args, **kwargs):
        import base64
        from .models import CfdiDocument
        from apps.integrations.odoo.models import OdooConnection, OdooSyncLog
        from apps.fiscal.odoo.client import create_client_from_connection, OdooClientError
        from apps.fiscal.odoo.sync_service import CfdiXmlParser

        connection = OdooConnection.objects.filter(
            empresa=self.empresa, status='active'
        ).first()

        if not connection:
            return HttpResponse('<div class="alert alert-warning">Sin conexión activa a Odoo</div>')

        try:
            client = create_client_from_connection(connection)
            invoices = client.search_read(
                'account.move',
                [
                    ['company_id', '=', connection.odoo_company_id],
                    ['l10n_mx_edi_cfdi_uuid', '!=', False],
                    ['move_type', 'in', ['in_invoice', 'out_invoice', 'in_refund', 'out_refund']]
                ],
                fields=['id', 'name', 'l10n_mx_edi_cfdi_uuid', 'amount_total', 'partner_id', 'move_type'],
                limit=50
            )

            imported = 0
            skipped = 0
            errors = []

            for invoice in invoices:
                uuid = invoice['l10n_mx_edi_cfdi_uuid']
                if CfdiDocument.objects.filter(company=self.empresa, uuid=uuid).exists():
                    skipped += 1
                    continue

                attachment = client.get_invoice_attachment(invoice['id'], 'xml')
                if not attachment or not attachment.get('datas'):
                    errors.append(f"Sin XML: {uuid[:8]}...")
                    continue

                try:
                    xml_content = base64.b64decode(attachment['datas']).decode('utf-8')
                    cfdi_data = CfdiXmlParser.parse(xml_content)
                    CfdiDocument.objects.create(
                        uuid=cfdi_data.uuid,
                        company=self.empresa,
                        rfc_emisor=cfdi_data.rfc_emisor,
                        rfc_receptor=cfdi_data.rfc_receptor,
                        tipo_cfdi=cfdi_data.tipo_comprobante,
                        total=cfdi_data.total,
                        moneda=cfdi_data.moneda,
                        fecha_emision=cfdi_data.fecha,
                        metodo_pago=cfdi_data.metodo_pago,
                        source='Proveedor',
                    )
                    OdooSyncLog.objects.create(
                        connection=connection,
                        cfdi_uuid=uuid,
                        direction='from_odoo',
                        status='success',
                        odoo_invoice_id=invoice['id'],
                        action_taken='imported',
                    )
                    imported += 1
                except Exception as e:
                    errors.append(f"{uuid[:8]}: {str(e)[:30]}")
                    OdooSyncLog.objects.create(
                        connection=connection,
                        cfdi_uuid=uuid,
                        direction='from_odoo',
                        status='error',
                        error_message=str(e),
                    )

            html = f'''
            <div class="alert alert-success shadow-lg mb-4">
                <div>
                    <h3 class="font-bold">Importación completada</h3>
                    <p class="text-sm">Importados: {imported} | Omitidos: {skipped} | Encontrados en Odoo: {len(invoices)}</p>
                </div>
            </div>
            '''
            if errors:
                html += f'<div class="alert alert-warning"><pre class="text-xs">{chr(10).join(errors[:5])}</pre></div>'
            return HttpResponse(html)

        except OdooClientError as e:
            return HttpResponse(f'<div class="alert alert-error">{e}</div>')


@method_decorator(login_required, name='dispatch')
@method_decorator(require_POST, name='dispatch')
class OdooExportView(TenantMixin, View):
    """Sincroniza CFDIs de Aspeia hacia Odoo (HTMX)."""

    def post(self, request, *args, **kwargs):
        from django.core.files.storage import default_storage
        from django.utils import timezone
        from .models import CfdiDocument
        from apps.integrations.odoo.models import OdooConnection, OdooSyncLog
        from apps.fiscal.odoo.sync_service import OdooInvoiceSyncService

        connection = OdooConnection.objects.filter(
            empresa=self.empresa, status='active'
        ).first()

        if not connection:
            return HttpResponse('<div class="alert alert-warning">Sin conexión activa a Odoo</div>')

        synced_uuids = OdooSyncLog.objects.filter(
            connection=connection,
            status='success',
            direction='to_odoo'
        ).values_list('cfdi_uuid', flat=True)

        pending_cfdis = CfdiDocument.objects.filter(
            company=self.empresa
        ).exclude(uuid__in=synced_uuids)[:50]

        if not pending_cfdis.exists():
            return HttpResponse('''
                <div class="alert alert-info shadow-lg">
                    <h3 class="font-bold">Sin CFDIs pendientes</h3>
                    <p class="text-sm">Todos los CFDIs ya están sincronizados con Odoo.</p>
                </div>
            ''')

        synced = 0
        exists = 0
        errors = []
        service = OdooInvoiceSyncService(connection)

        for cfdi in pending_cfdis:
            try:
                xml_content = None
                if hasattr(cfdi, 's3_xml_path') and cfdi.s3_xml_path:
                    try:
                        with default_storage.open(cfdi.s3_xml_path, 'rb') as f:
                            xml_content = f.read().decode('utf-8')
                    except Exception:
                        pass
                result = service.sync_cfdi_to_odoo(str(cfdi.uuid), xml_content)
                if result.get('status') == 'exists':
                    exists += 1
                elif result.get('status') == 'created':
                    synced += 1
                else:
                    errors.append(f"{str(cfdi.uuid)[:8]}: {result.get('message', 'Error')[:30]}")
            except Exception as e:
                errors.append(f"{str(cfdi.uuid)[:8]}: {str(e)[:30]}")

        connection.last_sync = timezone.now()
        connection.save()

        total_pending = CfdiDocument.objects.filter(
            company=self.empresa
        ).exclude(uuid__in=synced_uuids).count()
        remaining = max(0, total_pending - 50)

        html = f'''
        <div class="alert alert-success shadow-lg mb-4">
            <h3 class="font-bold">Sincronización completada</h3>
            <p class="text-sm">Creados: {synced} | Ya existían: {exists} | Errores: {len(errors)}</p>
        </div>
        '''
        if remaining > 0:
            html += f'<div class="alert alert-info mb-4">Quedan {remaining} CFDIs pendientes. Haz clic de nuevo para continuar.</div>'
        if errors:
            html += f'<div class="alert alert-warning"><pre class="text-xs">{chr(10).join(errors[:5])}</pre></div>'
        return HttpResponse(html)

