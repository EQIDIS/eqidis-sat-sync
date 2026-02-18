"""
Tareas Celery para sincronización con Odoo.
"""
from celery import shared_task
from django.utils import timezone
from django.core.files.storage import default_storage
import logging

from apps.integrations.odoo.models import OdooConnection, OdooSyncLog

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def sync_cfdi_to_odoo_task(self, empresa_id: int, cfdi_uuid: str,
                            xml_content: str = None, auto_post: bool = True):
    """Tarea Celery para sincronizar un CFDI hacia Odoo."""
    from .sync_service import sync_cfdi_to_odoo

    logger.info(f"Iniciando sincronización CFDI {cfdi_uuid} para empresa {empresa_id}")

    try:
        result = sync_cfdi_to_odoo(empresa_id, cfdi_uuid, xml_content, auto_post)
        logger.info(f"Resultado sincronización: {result}")
        return result
    except Exception as e:
        logger.exception(f"Error en tarea de sincronización: {e}")
        raise self.retry(exc=e)


@shared_task
def sync_pending_cfdis_task(empresa_id: int = None, limit: int = 100):
    """Sincroniza CFDIs pendientes hacia Odoo."""
    from apps.fiscal.models import CfdiDocument

    connections = OdooConnection.objects.filter(status='active', auto_sync_enabled=True)
    if empresa_id:
        connections = connections.filter(empresa_id=empresa_id)

    results = []
    for connection in connections:
        synced_uuids = OdooSyncLog.objects.filter(
            connection=connection,
            status='success'
        ).values_list('cfdi_uuid', flat=True)

        pending_cfdis = CfdiDocument.objects.filter(
            company=connection.empresa
        ).exclude(
            uuid__in=synced_uuids
        )[:limit]

        logger.info(f"Encontrados {pending_cfdis.count()} CFDIs pendientes para {connection.empresa}")

        for cfdi in pending_cfdis:
            sync_cfdi_to_odoo_task.delay(
                empresa_id=connection.empresa_id,
                cfdi_uuid=str(cfdi.uuid)
            )
            results.append(str(cfdi.uuid))

    return {
        'queued': len(results),
        'cfdis': results
    }


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


@shared_task
def sync_new_cfdis_to_odoo(empresa_id: int, request_id: int = None):
    """Sincroniza CFDIs nuevos de una descarga SAT hacia Odoo."""
    from apps.fiscal.models import CfdiDocument
    from .sync_service import OdooInvoiceSyncService

    logger.info(f"Sincronizando CFDIs nuevos a Odoo para empresa {empresa_id}")

    connection = OdooConnection.objects.filter(
        empresa_id=empresa_id,
        status='active',
        auto_sync_enabled=True
    ).first()

    if not connection:
        logger.info(f"No hay conexión Odoo activa para empresa {empresa_id}")
        return {'status': 'skipped', 'reason': 'No hay conexión Odoo activa'}

    synced_uuids = OdooSyncLog.objects.filter(
        connection=connection,
        status='success'
    ).values_list('cfdi_uuid', flat=True)

    cfdis_qs = CfdiDocument.objects.filter(company_id=empresa_id).exclude(uuid__in=synced_uuids)

    if request_id:
        cfdis_qs = cfdis_qs.filter(download_package__request_id=request_id)

    cfdis = cfdis_qs[:50]

    logger.info(f"Encontrados {cfdis.count()} CFDIs para sincronizar")

    synced = 0
    exists = 0
    errors = 0

    service = OdooInvoiceSyncService(connection)

    for cfdi in cfdis:
        try:
            xml_content = None
            if cfdi.s3_xml_path:
                try:
                    with default_storage.open(cfdi.s3_xml_path, 'rb') as f:
                        xml_content = f.read().decode('utf-8')
                except Exception as e:
                    logger.warning(f"No se pudo leer XML de {cfdi.uuid}: {e}")

            result = service.sync_cfdi_to_odoo(str(cfdi.uuid), xml_content)

            if result['status'] == 'created':
                synced += 1
                cfdi.creado_en_sistema = True
                cfdi.save(update_fields=['creado_en_sistema'])
            elif result['status'] == 'exists':
                exists += 1
                if not cfdi.creado_en_sistema:
                    cfdi.creado_en_sistema = True
                    cfdi.save(update_fields=['creado_en_sistema'])
            else:
                errors += 1

        except Exception as e:
            logger.error(f"Error sincronizando CFDI {cfdi.uuid}: {e}")
            errors += 1

    connection.last_sync = timezone.now()
    connection.save()

    logger.info(f"Sincronización completada: {synced} creados, {exists} existían, {errors} errores")

    return {
        'status': 'completed',
        'synced': synced,
        'exists': exists,
        'errors': errors,
        'total': cfdis.count()
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
