"""
Tareas asíncronas para integración SAT.
Ejecutadas por Celery workers.
"""
import hashlib
import logging
from datetime import date

from celery import shared_task
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone

from apps.companies.models import Empresa
from apps.fiscal.models import CfdiCertificate, CfdiDownloadRequest, SatDownloadPackage

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def solicitar_descarga_sat(self, empresa_id, fecha_inicio, fecha_fin, tipo='recibidos', user_id=None, is_auto_generated=False):
    """
    Paso 1: Crea solicitud de descarga al SAT.
    
    Args:
        empresa_id: ID de la empresa
        fecha_inicio: Fecha inicio del rango (YYYY-MM-DD)
        fecha_fin: Fecha fin del rango (YYYY-MM-DD)
        tipo: 'emitidos' o 'recibidos'
        user_id: ID del usuario que solicita (opcional)
        is_auto_generated: True si es por sincronización automática
    """
    from apps.integrations.sat.client import SATClient, SATClientError
    
    logger.info(f"Iniciando solicitud descarga SAT para empresa {empresa_id}: {fecha_inicio} - {fecha_fin}")
    
    try:
        empresa = Empresa.objects.get(id=empresa_id)
        
        # Obtener FIEL activa
        fiel = CfdiCertificate.objects.filter(
            company=empresa, tipo='FIEL', status='active'
        ).first()
        
        if not fiel:
            logger.error(f"No hay FIEL activa para empresa {empresa_id}")
            return {'error': 'No hay FIEL activa configurada', 'success': False}
        
        # Crear cliente SAT
        client = SATClient(fiel)
        
        # Determinar parámetros según tipo
        if tipo == 'emitidos':
            result = client.solicitar_descarga(
                fecha_inicio=fecha_inicio,
                fecha_fin=fecha_fin,
                tipo='emitidos',
                rfc_emisor=empresa.rfc,
            )
        else:  # recibidos
            result = client.solicitar_descarga(
                fecha_inicio=fecha_inicio,
                fecha_fin=fecha_fin,
                tipo='recibidos',
                rfc_receptor=empresa.rfc,
            )
        
        # Actualizar timestamp de última sincronización en la empresa
        empresa.last_mass_sync = timezone.now()
        empresa.save(update_fields=['last_mass_sync'])
        
        # Crear registro de solicitud
        request = CfdiDownloadRequest.objects.create(
            company=empresa,
            requested_by_id=user_id,
            fecha_inicio=fecha_inicio,
            fecha_fin=fecha_fin,
            tipo=tipo,
            status='requested',
            request_id_sat=result.get('id_solicitud'),
            sat_response_raw=str(result),
            is_auto_generated=is_auto_generated,
        )
        
        logger.info(f"Solicitud SAT creada: {request.id} - ID SAT: {result.get('id_solicitud')}")
        
        return {
            'success': True,
            'request_id': request.id,
            'id_solicitud_sat': result.get('id_solicitud'),
            'cod_estatus': result.get('cod_estatus'),
            'mensaje': result.get('mensaje'),
        }
        
    except SATClientError as e:
        logger.error(f"Error cliente SAT: {e}")
        return {'error': str(e), 'success': False}
    except Exception as exc:
        logger.exception(f"Error inesperado en solicitud SAT: {exc}")
        raise self.retry(exc=exc, countdown=60)


@shared_task
def verificar_solicitudes_pendientes():
    """
    Paso 2: Verifica estado de todas las solicitudes pendientes.
    Cron job que corre cada 5 minutos.
    """
    from apps.integrations.sat.client import SATClient, SATClientError
    
    pendientes = CfdiDownloadRequest.objects.filter(
        status__in=['requested', 'ready']
    ).exclude(request_id_sat__isnull=True)
    
    logger.info(f"Verificando {pendientes.count()} solicitudes pendientes")
    
    for solicitud in pendientes:
        try:
            # Obtener FIEL de la empresa
            fiel = CfdiCertificate.objects.filter(
                company=solicitud.company, tipo='FIEL', status='active'
            ).first()
            
            if not fiel:
                logger.warning(f"Solicitud {solicitud.id}: no hay FIEL activa")
                continue
            
            client = SATClient(fiel)
            result = client.verificar_solicitud(solicitud.request_id_sat)
            
            estado = result.get('estado', '')
            logger.info(f"Solicitud {solicitud.id}: estado={estado}")
            
            # satcfdi devuelve:
            # 1 = Aceptada
            # 2 = En Proceso
            # 3 = Terminada
            # 4 = Error
            # 5 = Rechazada
            # 6 = Vencida
            
            # Actualizar solicitud según estado
            if estado in ['Terminada', 'Terminado', '3', 3, 'EstadoSolicitud.TERMINADA']:
                solicitud.status = 'ready'
                solicitud.sat_response_raw = str(result)
                solicitud.save()
                
                # Crear registros para cada paquete
                for pkg_id in result.get('paquetes', []):
                    SatDownloadPackage.objects.get_or_create(
                        request=solicitud,
                        package_id_sat=pkg_id,
                        defaults={'status': 'pending'}
                    )
                
                # Encolar descarga de paquetes
                for pkg in solicitud.packages.filter(status='pending'):
                    descargar_paquete_sat.delay(pkg.id)
                    
            elif estado in ['Error', 'Rechazada', 'Rechazado', '4', '5', 4, 5]:
                solicitud.status = 'failed'
                solicitud.sat_response_raw = str(result)
                solicitud.save()
                
            elif estado in ['Vencida', '6', 6]:
                solicitud.status = 'failed'
                solicitud.sat_response_raw = f"Solicitud vencida: {result}"
                solicitud.save()
                
            # Estados '1', '2' (Aceptada, EnProceso) no requieren acción
                
        except SATClientError as e:
            logger.error(f"Error verificando solicitud {solicitud.id}: {e}")
        except Exception as e:
            logger.exception(f"Error inesperado verificando solicitud {solicitud.id}: {e}")


@shared_task(bind=True, max_retries=3)
def descargar_paquete_sat(self, package_id):
    """
    Paso 3: Descarga un paquete ZIP específico y lo guarda en S3.
    """
    from apps.integrations.sat.client import SATClient, SATClientError
    
    logger.info(f"Descargando paquete {package_id}")
    
    try:
        package = SatDownloadPackage.objects.select_related('request__company').get(id=package_id)
        package.status = 'downloading'
        package.save()
        
        empresa = package.request.company
        
        # Obtener FIEL
        fiel = CfdiCertificate.objects.filter(
            company=empresa, tipo='FIEL', status='active'
        ).first()
        
        if not fiel:
            package.status = 'failed'
            package.error_message = 'No hay FIEL activa'
            package.save()
            return {'error': 'No hay FIEL activa', 'success': False}
        
        client = SATClient(fiel)
        
        # Descargar paquete
        zip_content = client.descargar_paquete(package.package_id_sat)
        
        # Calcular hash y tamaño
        file_hash = hashlib.sha256(zip_content).hexdigest()
        file_size = len(zip_content)
        
        # Guardar en S3
        s3_path = f"sat/packages/{empresa.id}/{package.package_id_sat}.zip"
        default_storage.save(s3_path, ContentFile(zip_content))
        
        # Actualizar registro
        package.s3_zip_path = s3_path
        package.file_hash = file_hash
        package.file_size = file_size
        package.status = 'downloaded'
        package.save()
        
        logger.info(f"Paquete {package_id} descargado: {file_size} bytes")
        
        # Encolar procesamiento de XMLs
        procesar_paquete_xml.delay(package_id)
        
        return {'success': True, 'package_id': package_id, 'size': file_size}
        
    except SATClientError as e:
        logger.error(f"Error SAT descargando paquete {package_id}: {e}")
        package.status = 'failed'
        package.error_message = str(e)
        package.retry_count += 1
        package.save()
        raise self.retry(exc=e, countdown=120)
    except Exception as exc:
        logger.exception(f"Error descargando paquete {package_id}: {exc}")
        raise self.retry(exc=exc, countdown=120)


@shared_task
def procesar_paquete_xml(package_id):
    """
    Paso 4: Extrae XMLs del ZIP y crea CfdiDocuments.
    """
    from apps.fiscal.cfdi_parser import CFDIParser, CFDIParseError
    
    logger.info(f"Procesando XMLs del paquete {package_id}")
    
    try:
        package = SatDownloadPackage.objects.select_related('request__company').get(id=package_id)
        package.status = 'processing'
        package.save()
        
        empresa = package.request.company
        parser = CFDIParser()
        
        # Leer ZIP desde S3
        with default_storage.open(package.s3_zip_path, 'rb') as f:
            zip_content = f.read()
        
        # Extraer XMLs
        import zipfile
        from io import BytesIO
        
        xmls = []
        xml_names = []
        with zipfile.ZipFile(BytesIO(zip_content), 'r') as zf:
            for filename in zf.namelist():
                if filename.lower().endswith('.xml'):
                    xmls.append(zf.read(filename))
                    xml_names.append(filename)
        
        package.cfdi_count = len(xmls)
        logger.info(f"Paquete {package_id}: {len(xmls)} XMLs encontrados")
        
        # Procesar cada XML
        processed = 0
        created = 0
        errors = 0
        
        for i, xml_bytes in enumerate(xmls):
            try:
                # Generar path S3 para el XML individual
                xml_name = xml_names[i] if i < len(xml_names) else f"cfdi_{i}.xml"
                s3_xml_path = f"sat/cfdi/{empresa.id}/{package.package_id_sat}/{xml_name}"
                
                # Guardar XML en S3
                default_storage.save(s3_xml_path, ContentFile(xml_bytes))
                
                # Parsear y guardar en BD
                document, was_created = parser.parse_and_save(
                    xml_content=xml_bytes,
                    empresa=empresa,
                    package=package,
                    s3_path=s3_xml_path
                )
                
                processed += 1
                if was_created:
                    created += 1
                    
            except CFDIParseError as e:
                logger.warning(f"Error parseando XML {i} en paquete {package_id}: {e}")
                errors += 1
            except Exception as e:
                logger.error(f"Error inesperado procesando XML {i} en paquete {package_id}: {e}")
                errors += 1
        
        package.cfdi_processed = processed
        package.status = 'completed'
        package.completed_at = timezone.now()
        package.save()
        
        # Actualizar solicitud padre si todos los paquetes están completos
        request = package.request
        all_completed = not request.packages.exclude(status='completed').exists()
        if all_completed:
            request.status = 'downloaded'
            request.completed_at = timezone.now()
            request.save()
            
            # Sincronizar a Odoo automáticamente si está habilitado
            try:
                from apps.fiscal.models import EmpresaSyncSettings
                sync_settings = EmpresaSyncSettings.objects.filter(company=empresa).first()
                if sync_settings and sync_settings.sync_to_odoo_enabled:
                    from apps.fiscal.odoo.tasks import sync_new_cfdis_to_odoo
                    # Ejecutar inmediatamente (sin delay)
                    logger.info(f"Disparando sincronización a Odoo para empresa {empresa.id}")
                    sync_new_cfdis_to_odoo.delay(empresa.id)
            except Exception as e:
                logger.warning(f"Error al disparar sync Odoo: {e}")
        
        logger.info(f"Paquete {package_id} procesado: {processed} CFDIs ({created} nuevos, {errors} errores)")
        
        return {
            'success': True,
            'processed': processed,
            'created': created,
            'duplicates': processed - created,
            'errors': errors,
            'total': len(xmls)
        }
        
    except Exception as e:
        logger.exception(f"Error procesando paquete {package_id}: {e}")
        package.status = 'failed'
        package.error_message = str(e)
        package.save()
        return {'error': str(e), 'success': False}


@shared_task
def validar_estado_cfdis_pendientes():
    """
    Valida el estado de CFDIs vigentes ante el SAT.
    
    Estrategia escalonada para eficiencia:
    - CFDIs 0-30 días: valida siempre (diario)
    - CFDIs 30-90 días: valida si es lunes (semanal)
    - CFDIs 90-365 días: valida si es día 1 del mes (mensual)  
    - CFDIs > 1 año: no valida (ya no se pueden cancelar según SAT)
    
    Procesa en batches de 100 por empresa para no saturar el SAT.
    """
    from datetime import timedelta
    from apps.fiscal.models import CfdiDocument, CfdiStateCheck
    from apps.integrations.sat.client import SATClient, SATClientError
    from apps.companies.models import Empresa
    
    now = timezone.now()
    today = now.date()
    weekday = today.weekday()  # 0=Lunes
    day_of_month = today.day
    
    year_ago = today - timedelta(days=365)
    month_ago = today - timedelta(days=30)
    quarter_ago = today - timedelta(days=90)
    
    empresas = Empresa.objects.filter(is_active=True)
    total_validated = 0
    total_changes = 0
    
    for empresa in empresas:
        # Obtener FIEL de la empresa
        fiel = CfdiCertificate.objects.filter(
            company=empresa, tipo='FIEL', status='active'
        ).first()
        
        if not fiel:
            logger.debug(f"Empresa {empresa.id}: sin FIEL activa, omitiendo")
            continue
        
        try:
            client = SATClient(fiel)
        except Exception as e:
            logger.warning(f"Empresa {empresa.id}: error inicializando cliente SAT: {e}")
            continue
        
        # Construir queryset según día de la semana
        # Base: solo CFDIs vigentes del último año
        base_qs = CfdiDocument.objects.filter(
            company=empresa,
            estado_sat='Vigente',
            fecha_emision__gte=year_ago,
        )
        
        # Determinar qué CFDIs validar según fecha
        if day_of_month == 1:
            # Día 1: validar todos (0-365 días)
            cfdis_to_validate = base_qs
        elif weekday == 0:
            # Lunes: validar 0-90 días
            cfdis_to_validate = base_qs.filter(fecha_emision__gte=quarter_ago)
        else:
            # Otros días: solo 0-30 días
            cfdis_to_validate = base_qs.filter(fecha_emision__gte=month_ago)
        
        # Limitar batch por empresa
        cfdis = cfdis_to_validate.order_by('-fecha_emision')[:100]
        
        logger.info(f"Empresa {empresa.id}: validando {cfdis.count()} CFDIs")
        
        for cfdi in cfdis:
            try:
                # Formatear total como string con 2 decimales
                total_str = f"{cfdi.total:.2f}"
                
                result = client.validar_estado_cfdi(
                    rfc_emisor=cfdi.rfc_emisor,
                    rfc_receptor=cfdi.rfc_receptor,
                    total=total_str,
                    uuid=str(cfdi.uuid),
                )
                
                estado_anterior = cfdi.estado_sat
                estado_nuevo = result.get('estado')
                es_cambio = estado_anterior != estado_nuevo
                
                # Crear registro de auditoría
                CfdiStateCheck.objects.create(
                    document=cfdi,
                    certificate=fiel,
                    estado_anterior=estado_anterior,
                    estado_sat=estado_nuevo or 'Desconocido',
                    estado_cancelacion=result.get('estado_cancelacion'),
                    es_cambio=es_cambio,
                    source='uuid_check',
                    response_raw=result.get('response_raw'),
                )
                
                # Actualizar documento si cambió
                if es_cambio and estado_nuevo:
                    cfdi.estado_sat = estado_nuevo
                    if estado_nuevo == 'Cancelado':
                        cfdi.fecha_cancelacion = now
                    cfdi.save(update_fields=['estado_sat', 'fecha_cancelacion'])
                    total_changes += 1
                    logger.info(f"CFDI {cfdi.uuid}: {estado_anterior} → {estado_nuevo}")
                
                total_validated += 1
                
            except SATClientError as e:
                logger.warning(f"Error validando CFDI {cfdi.uuid}: {e}")
            except Exception as e:
                logger.exception(f"Error inesperado validando CFDI {cfdi.uuid}: {e}")
    
    logger.info(f"Validación completada: {total_validated} CFDIs, {total_changes} cambios")
    return {
        'validated': total_validated,
        'changes': total_changes,
    }


@shared_task
def sincronizar_cfdis_recientes():
    """
    Tarea cron HORA (cada hora).
    Busca empresas con auto_sync_enabled=True.
    Verifica si (now - last_sync) >= sync_frequency_hours.
    Si sí, solicita descarga.
    """
    from datetime import date, timedelta
    from apps.fiscal.models import EmpresaSyncSettings
    
    logger.info("Iniciando ciclo de sincronización automática de CFDIs...")
    
    settings = EmpresaSyncSettings.objects.filter(
        auto_sync_enabled=True,
        company__is_active=True
    ).select_related('company')
    
    now = timezone.now()
    today = date.today()
    # Últimos 3 días para asegurar cobertura (el SAT a veces tarda en disponibilizar)
    start_date = today - timedelta(days=3)
    
    count = 0
    skipped = 0
    
    for setting in settings:
        empresa = setting.company
        
        # Lógica simplificada: Solo ejecutar si la hora coincide
        # El campo scheduled_start_hour ahora es obligatorio (default 3)
        if now.hour != setting.scheduled_start_hour:
            # No es la hora programada para esta empresa
            skipped += 1
            continue
        
        logger.info(f"Sincronizando empresa {empresa.nombre} ({empresa.id}) - Hora programada: {setting.scheduled_start_hour}:00...")
        
        # Solicitar RECIBIDOS - Automático
        solicitar_descarga_sat.delay(
            empresa_id=empresa.id,
            fecha_inicio=start_date.isoformat(),
            fecha_fin=today.isoformat(),
            tipo='recibidos',
            user_id=None,
            is_auto_generated=True
        )
        
        # Solicitar EMITIDOS - Automático
        solicitar_descarga_sat.delay(
            empresa_id=empresa.id,
            fecha_inicio=start_date.isoformat(),
            fecha_fin=today.isoformat(),
            tipo='emitidos',
            user_id=None,
            is_auto_generated=True
        )
        
        # Actualizar timestamp de intento
        setting.last_sync_at = now
        setting.save(update_fields=['last_sync_at'])
        
        count += 1
        
    logger.info(f"Ciclo completado: {count} empresas sincronizadas, {skipped} saltadas (aún en periodo de espera).")


@shared_task
def ejecutar_sincronizacion_semanal():
    """
    Tarea semanal que:
    1. Para cada empresa con weekly_sync_enabled=True
    2. Descarga CFDIs emitidos y recibidos de la semana anterior
    3. Si sync_to_odoo_enabled=True, sincroniza los CFDIs a Odoo
    
    Se ejecuta cada hora pero verifica internamente si es el día/hora configurado
    para cada empresa.
    
    El rango de descarga es siempre la semana anterior completa (lunes a domingo).
    """
    from datetime import date, timedelta
    from apps.fiscal.models import EmpresaSyncSettings
    from apps.fiscal.odoo.tasks import sync_new_cfdis_to_odoo
    
    # IMPORTANTE: Usar localtime para comparar con hora local de México, no UTC
    now = timezone.localtime()
    today = now.date()
    weekday = today.weekday()  # 0=Lunes, 6=Domingo
    
    logger.info(f"Iniciando ciclo de sincronización semanal... Hora local: {now.strftime('%H:%M')}")
    
    settings = EmpresaSyncSettings.objects.filter(
        weekly_sync_enabled=True,
        company__is_active=True
    ).select_related('company')
    
    count = 0
    skipped = 0
    
    for setting in settings:
        # Verificar si es el día, hora Y minuto correcto para esta empresa
        # Usamos una ventana de 5 minutos para dar flexibilidad
        current_minute = now.minute
        target_minute = setting.weekly_sync_minute
        minute_match = abs(current_minute - target_minute) <= 2  # Ventana de +/- 2 minutos
        
        if weekday != setting.weekly_sync_day or now.hour != setting.weekly_sync_hour or not minute_match:
            skipped += 1
            continue
        
        empresa = setting.company
        
        # Calcular rango de fechas usando la configuración de días
        days_range = setting.weekly_sync_days_range or 7
        fecha_fin = today - timedelta(days=1)  # Ayer
        fecha_inicio = today - timedelta(days=days_range)
        
        logger.info(f"Procesando empresa {empresa.nombre} ({empresa.id}) - Rango: {fecha_inicio} a {fecha_fin} ({days_range} días)")
        
        # 1. Solicitar descarga de RECIBIDOS
        solicitar_descarga_sat.delay(
            empresa_id=empresa.id,
            fecha_inicio=fecha_inicio.isoformat(),
            fecha_fin=fecha_fin.isoformat(),
            tipo='recibidos',
            user_id=None,
            is_auto_generated=True
        )
        
        # 2. Solicitar descarga de EMITIDOS
        solicitar_descarga_sat.delay(
            empresa_id=empresa.id,
            fecha_inicio=fecha_inicio.isoformat(),
            fecha_fin=fecha_fin.isoformat(),
            tipo='emitidos',
            user_id=None,
            is_auto_generated=True
        )
        
        # NOTA: La sincronización a Odoo ahora ocurre automáticamente cuando
        # se completan los paquetes de descarga (ver procesar_paquete_xml).
        # Ya no es necesario programarla con delay aquí.
        
        # Actualizar timestamp
        setting.last_weekly_sync_at = now
        setting.save(update_fields=['last_weekly_sync_at'])
        
        count += 1
    
    logger.info(f"Sincronización semanal completada: {count} empresas procesadas, {skipped} saltadas")
    return {'processed': count, 'skipped': skipped}

