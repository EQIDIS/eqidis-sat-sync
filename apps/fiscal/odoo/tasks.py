"""
Tareas Celery para sincronización con Odoo.

Arquitectura de locks:
- sync_new_cfdis_to_odoo: Lock por empresa (cache Redis) para evitar
  ejecución concurrente. Si el lock está tomado, se absorbe silenciosamente
  porque la tarea en curso re-consulta el DB en cada bache y recoge CFDIs
  nuevos que se hayan agregado durante el procesamiento.
- sync_cfdi_to_odoo_task: Lock por UUID+empresa para tareas individuales.
"""
from celery import shared_task
from django.utils import timezone
from django.core.files.storage import default_storage
from django.core.cache import cache
import logging
import time

from apps.integrations.odoo.models import OdooConnection, OdooSyncLog

logger = logging.getLogger(__name__)

# Lock timeout: 20 minutos (cubre el time_limit de la tarea + margen)
SYNC_LOCK_TIMEOUT = 1200


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def sync_cfdi_to_odoo_task(self, empresa_id: int, cfdi_uuid: str,
                            xml_content: str = None, auto_post: bool = True):
    """Tarea Celery para sincronizar un CFDI individual hacia Odoo."""
    from .sync_service import sync_cfdi_to_odoo

    # Lock por UUID+empresa para evitar procesamiento concurrente del mismo CFDI
    lock_key = f"sync_cfdi_lock_{empresa_id}_{cfdi_uuid}"
    acquired = cache.add(lock_key, True, 120)
    if not acquired:
        logger.info(f"CFDI {cfdi_uuid} ya está siendo procesado para empresa {empresa_id}")
        return {'status': 'skipped', 'reason': 'Ya en procesamiento'}

    try:
        result = sync_cfdi_to_odoo(empresa_id, cfdi_uuid, xml_content, auto_post)
        return result
    except Exception as e:
        logger.exception(f"Error en tarea de sincronización CFDI {cfdi_uuid}: {e}")
        raise self.retry(exc=e)
    finally:
        cache.delete(lock_key)


@shared_task
def sync_pending_cfdis_task(empresa_id: int = None, limit: int = 100):
    """Sincroniza CFDIs pendientes hacia Odoo (dispara tareas individuales)."""
    from apps.fiscal.models import CfdiDocument

    connections = OdooConnection.objects.filter(status='active', auto_sync_enabled=True)
    if empresa_id:
        connections = connections.filter(empresa_id=empresa_id)

    results = []
    for connection in connections:
        synced_uuids = set(OdooSyncLog.objects.filter(
            connection=connection,
            status='success'
        ).values_list('cfdi_uuid', flat=True))

        # También excluir UUIDs con logs pending (ya en cola)
        pending_log_uuids = set(OdooSyncLog.objects.filter(
            connection=connection,
            status='pending'
        ).values_list('cfdi_uuid', flat=True))

        pending_cfdis = CfdiDocument.objects.filter(
            company=connection.empresa
        ).exclude(
            uuid__in=synced_uuids | pending_log_uuids
        )[:limit]

        queued_uuids = set()
        for cfdi in pending_cfdis:
            cfdi_uuid_str = str(cfdi.uuid)
            if cfdi_uuid_str in queued_uuids:
                continue
            queued_uuids.add(cfdi_uuid_str)

            sync_cfdi_to_odoo_task.delay(
                empresa_id=connection.empresa_id,
                cfdi_uuid=cfdi_uuid_str
            )
            results.append(cfdi_uuid_str)

    return {'queued': len(results), 'cfdis': results}


@shared_task
def verify_odoo_connection_task(connection_id: int):
    """Verifica que una conexión Odoo esté funcionando."""
    from .client import create_client_from_connection, OdooClientError

    try:
        connection = OdooConnection.objects.get(id=connection_id)
        client = create_client_from_connection(connection)
        version = client.get_version()

        connection.status = 'active'
        connection.last_error = None
        connection.save()

        return {
            'status': 'success',
            'version': version.get('server_version'),
            'message': 'Conexión verificada exitosamente'
        }

    except OdooConnection.DoesNotExist:
        return {'status': 'error', 'message': 'Conexión no encontrada'}
    except OdooClientError as e:
        connection.status = 'error'
        connection.last_error = str(e)
        connection.save()
        return {'status': 'error', 'message': str(e)}


@shared_task(bind=True, time_limit=1200, soft_time_limit=1140)
def sync_new_cfdis_to_odoo(self, empresa_id: int, request_id: int = None,
                           exclude_uuids: list = None, force_sync: bool = False):
    """
    Sincroniza CFDIs hacia Odoo en baches internos de 50.

    Procesa TODOS los CFDIs pendientes iterando en lotes de 50.
    El loop re-consulta el DB en cada iteración para recoger CFDIs
    que se hayan agregado por descargas concurrentes.

    Si otra tarea intenta correr para la misma empresa, se absorbe
    silenciosamente (la tarea en curso se encarga de todo).
    """
    logger.info(f"Sincronizando CFDIs a Odoo para empresa {empresa_id} (force={force_sync})")

    # --- Lock por empresa ---
    lock_key = f"sync_cfdis_odoo_lock_{empresa_id}"
    acquired = cache.add(lock_key, True, SYNC_LOCK_TIMEOUT)
    if not acquired:
        # La tarea en curso re-consulta el DB en cada bache, así que
        # recoge cualquier CFDI nuevo. No necesitamos reintentar.
        logger.info(
            f"Sincronización ya en curso para empresa {empresa_id}. "
            f"La tarea activa procesará los CFDIs pendientes."
        )
        return {'status': 'absorbed', 'reason': 'Otra tarea ya está procesando esta empresa'}

    try:
        return _sync_new_cfdis_to_odoo_inner(empresa_id, request_id, force_sync)
    finally:
        cache.delete(lock_key)


def _sync_new_cfdis_to_odoo_inner(empresa_id, request_id, force_sync):
    """Lógica interna de sincronización (protegida por lock)."""
    from apps.fiscal.models import CfdiDocument
    from .sync_service import OdooInvoiceSyncService

    # --- 1. Validar conexión ---
    connection = OdooConnection.objects.filter(
        empresa_id=empresa_id,
        status='active',
        auto_sync_enabled=True
    ).first()

    if not connection:
        logger.info(f"No hay conexión Odoo activa para empresa {empresa_id}")
        return {'status': 'skipped', 'reason': 'No hay conexión Odoo activa'}

    if not connection.password:
        logger.error(f"Contraseña de Odoo no legible para empresa {empresa_id}. Abortando.")
        return {'status': 'error', 'reason': 'Error de contraseña (InvalidToken)'}

    # --- 2. Procesar en baches ---
    BATCH_SIZE = 50
    MAX_BATCHES = 200  # Hasta 10,000 CFDIs por ejecución
    EMPTY_BATCH_WAIT = 3  # Segundos de espera cuando no hay CFDIs (por si están llegando)
    MAX_EMPTY_WAITS = 2  # Máximo de esperas vacías antes de salir

    total_synced = 0
    total_exists = 0
    total_errors = 0
    total_skipped = 0
    processed_uuids = set()
    batch_number = 0
    consecutive_empty = 0

    service = OdooInvoiceSyncService(connection)

    while batch_number < MAX_BATCHES:
        batch_number += 1

        # Queryset fresco en cada iteración (captura CFDIs agregados por descargas concurrentes)
        cfdis_qs = CfdiDocument.objects.filter(
            company_id=empresa_id
        ).exclude(tipo_cfdi='P')

        if not force_sync:
            cfdis_qs = cfdis_qs.filter(creado_en_sistema=False)

        if processed_uuids:
            cfdis_qs = cfdis_qs.exclude(uuid__in=list(processed_uuids))

        if request_id:
            cfdis_qs = cfdis_qs.filter(download_package__request_id=request_id)

        cfdis = list(cfdis_qs.order_by('id')[:BATCH_SIZE])

        if not cfdis:
            # Puede haber CFDIs llegando de una descarga concurrente.
            # Esperar brevemente y revisar una vez más antes de salir.
            if consecutive_empty < MAX_EMPTY_WAITS:
                consecutive_empty += 1
                logger.info(
                    f"Sin CFDIs pendientes. Esperando {EMPTY_BATCH_WAIT}s por descargas "
                    f"concurrentes ({consecutive_empty}/{MAX_EMPTY_WAITS})..."
                )
                time.sleep(EMPTY_BATCH_WAIT)
                continue
            else:
                logger.info(f"No quedan más CFDIs por procesar. Total baches: {batch_number - 1}")
                break

        consecutive_empty = 0  # Reset counter cuando encontramos CFDIs
        logger.info(f"Bache #{batch_number}: procesando {len(cfdis)} CFDIs...")

        for cfdi in cfdis:
            processed_uuids.add(cfdi.uuid)
            try:
                # Leer XML desde S3
                xml_content = None
                if cfdi.s3_xml_path:
                    try:
                        with default_storage.open(cfdi.s3_xml_path, 'rb') as f:
                            xml_content = f.read().decode('utf-8')
                    except Exception as e:
                        logger.warning(f"No se pudo leer XML de {cfdi.uuid}: {e}")

                # Si no hay XML, el servicio retornará error (XML requerido para crear)
                result = service.sync_cfdi_to_odoo(str(cfdi.uuid), xml_content)
                status = result.get('status', 'error')

                if status == 'created':
                    total_synced += 1
                    cfdi.creado_en_sistema = True
                    cfdi.save(update_fields=['creado_en_sistema'])
                elif status == 'exists':
                    total_exists += 1
                    if not cfdi.creado_en_sistema:
                        cfdi.creado_en_sistema = True
                        cfdi.save(update_fields=['creado_en_sistema'])
                elif status == 'skipped':
                    total_skipped += 1
                    cfdi.creado_en_sistema = True
                    cfdi.save(update_fields=['creado_en_sistema'])
                else:
                    total_errors += 1

            except Exception as e:
                logger.error(f"Error sincronizando CFDI {cfdi.uuid}: {e}")
                total_errors += 1

        logger.info(
            f"Bache #{batch_number} completado: "
            f"{total_synced} creados, {total_exists} existían, "
            f"{total_skipped} omitidos, {total_errors} errores"
        )

    # --- 3. Finalizar ---
    total_processed = total_synced + total_exists + total_skipped + total_errors
    logger.info(
        f"Sincronización FINALIZADA para empresa {empresa_id}: "
        f"{total_processed} procesados en {batch_number} baches "
        f"({total_synced} creados, {total_exists} existían, "
        f"{total_skipped} omitidos, {total_errors} errores)"
    )

    return {
        'status': 'completed',
        'synced': total_synced,
        'exists': total_exists,
        'skipped': total_skipped,
        'errors': total_errors,
        'total': total_processed,
        'batches': batch_number
    }


@shared_task
def sync_cfdi_status_to_odoo(cfdi_uuid: str, nuevo_estado: str):
    """Sincroniza el cambio de estado de un CFDI a Odoo."""
    from apps.fiscal.models import CfdiDocument
    from .client import create_client_from_connection, OdooClientError
    from .sync_service import map_sat_state_to_odoo

    logger.info(f"Sincronizando estado de CFDI {cfdi_uuid} a Odoo: {nuevo_estado}")

    try:
        cfdi = CfdiDocument.objects.get(uuid=cfdi_uuid)
    except CfdiDocument.DoesNotExist:
        return {'status': 'error', 'message': 'CFDI no encontrado'}

    connection = OdooConnection.objects.filter(
        empresa=cfdi.company,
        status='active'
    ).first()

    if not connection:
        return {'status': 'skipped', 'reason': 'Sin conexión Odoo'}

    try:
        client = create_client_from_connection(connection)
        invoice = client.find_invoice_by_uuid_extended(cfdi_uuid, connection.odoo_company_id)

        if not invoice:
            logger.info(f"CFDI {cfdi_uuid} no existe en Odoo, omitiendo actualización de estado")
            return {'status': 'skipped', 'reason': 'No existe en Odoo'}

        odoo_sat_state = map_sat_state_to_odoo(nuevo_estado)
        updated = client.update_cfdi_document_state(invoice['id'], odoo_sat_state)

        if updated:
            logger.info(f"Estado SAT actualizado para factura {invoice['id']}: {odoo_sat_state}")
        else:
            logger.warning(f"No se pudo actualizar estado SAT para factura {invoice['id']}")

        if odoo_sat_state == 'cancelled' and invoice.get('state') == 'draft':
            try:
                client.write('account.move', [invoice['id']], {
                    'l10n_mx_edi_cfdi_cancel': True
                })
                logger.info(f"Factura {invoice['id']} marcada como cancelada en Odoo")
            except OdooClientError:
                pass

        OdooSyncLog.objects.create(
            connection=connection,
            cfdi_uuid=cfdi_uuid,
            direction='to_odoo',
            status='success',
            odoo_invoice_id=invoice['id'],
            action_taken=f'status_updated:{odoo_sat_state}',
            response_data={
                'estado_original': nuevo_estado,
                'estado_odoo': odoo_sat_state,
                'updated': updated
            }
        )

        return {
            'status': 'success',
            'invoice_id': invoice['id'],
            'nuevo_estado': odoo_sat_state
        }

    except OdooClientError as e:
        logger.error(f"Error actualizando estado en Odoo: {e}")
        OdooSyncLog.objects.create(
            connection=connection,
            cfdi_uuid=cfdi_uuid,
            direction='to_odoo',
            status='error',
            error_message=str(e),
        )
        return {'status': 'error', 'message': str(e)}
