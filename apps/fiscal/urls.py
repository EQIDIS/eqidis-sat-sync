from django.urls import path
from . import views

app_name = 'fiscal'

urlpatterns = [
    path('dashboard/', views.FiscalDashboardView.as_view(), name='dashboard'),
    # Endpoints HTMX para carga de certificados
    path('upload-csd/', views.UploadCSDView.as_view(), name='upload_csd'),
    path('upload-fiel/', views.UploadFIELView.as_view(), name='upload_fiel'),
    # Mantener alias para compatibilidad
    path('subir-certificado/', views.FiscalDashboardView.as_view(), name='subir_certificado'),

    # Vista Unificada de CFDIs
    path('cfdis/', views.CfdiListView.as_view(), name='cfdis'),
    path('cfdis/tabla/', views.CfdiTablePartialView.as_view(), name='cfdis_table'),
    path('cfdis/stats/', views.CfdiStatsPartialView.as_view(), name='cfdis_stats'),
    path('cfdis/solicitudes-recientes/', views.CfdiSolicitudesRecientesPartialView.as_view(), name='cfdis_solicitudes_recientes'),
    path('cfdis/solicitudes/<int:pk>/detalle/', views.CfdiDownloadRequestDetailPartialView.as_view(), name='cfdis_solicitud_detalle'),
    path('cfdis/reset/', views.ResetCfdisView.as_view(), name='reset_cfdis'),
    path('cfdis/sync-manual/', views.EjecutarSyncManualView.as_view(), name='sync_manual'),
    path('cfdis/odoo/test-connection/', views.OdooTestConnectionView.as_view(), name='odoo_test_connection'),
    path('cfdis/odoo/companies/', views.OdooCompaniesJsonView.as_view(), name='odoo_companies_json'),
    path('cfdis/odoo/companies/options/', views.OdooCompaniesOptionsView.as_view(), name='odoo_companies_options'),
    path('cfdis/odoo/set-company/', views.OdooSetCompanyView.as_view(), name='odoo_set_company'),
    path('cfdis/odoo/import/', views.OdooImportView.as_view(), name='odoo_import'),
    path('cfdis/odoo/export/', views.OdooExportView.as_view(), name='odoo_export'),
    path('cfdis/odoo/sync-all/', views.OdooExportView.as_view(), name='odoo_sync_all'),
    path('cfdis/<str:uuid>/', views.CfdiDetalleView.as_view(), name='cfdi_detalle'),

    # Descarga Masiva SAT
    path('descargas/', views.DescargasListView.as_view(), name='descargas'),
    path('descargas/crear/', views.DescargasCrearView.as_view(), name='descargas_crear'),
    path('descargas/<int:pk>/', views.DescargasDetalleView.as_view(), name='descargas_detalle'),
    # Descarga de archivos
    path('descargas/paquete/<int:pk>/', views.DescargarPaqueteView.as_view(), name='descargar_paquete'),
    path('descargas/cfdi/<str:uuid>/', views.DescargarCfdiView.as_view(), name='descargar_cfdi'),
    # Cancelaciones pendientes
    path('cancelaciones-pendientes/', views.CancelacionesPendientesView.as_view(), name='cancelaciones_pendientes'),
    # API: Verificación 69-B
    path('api/verificar-69b/<str:rfc>/', views.Verificar69BView.as_view(), name='verificar_69b'),
    # API: Validar estado CFDI on-demand
    path('api/validar-cfdi/<str:uuid>/', views.ValidarEstadoCfdiView.as_view(), name='validar_cfdi'),
    # API: Validar todos los CFDIs de una solicitud
    path('api/validar-todos/<int:pk>/', views.ValidarTodosCfdiView.as_view(), name='validar_todos'),
    # Configuración de Sincronización Diaria
    path('update-sync-settings/', views.UpdateSyncSettingsView.as_view(), name='update_sync_settings'),
    # Configuración semanal y logs (usados desde cfdis)
    path('sync-semanal/update-settings/', views.UpdateWeeklySyncSettingsView.as_view(), name='update_weekly_sync_settings'),
    path('sync-semanal/logs/', views.SyncSemanalLogsView.as_view(), name='sync_semanal_logs'),
]


