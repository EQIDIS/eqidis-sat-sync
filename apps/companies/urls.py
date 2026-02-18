from django.urls import path
from . import views

app_name = 'companies'

urlpatterns = [
    # Root: Dashboard of the active company
    path('', views.CompanyDashboardView.as_view(), name='dashboard'),
    
    path('seleccionar-empresa/', views.seleccionar_empresa, name='seleccionar_empresa'),
    path('crear/', views.CompanyCreateView.as_view(), name='crear_empresa'),
    
    # Alias for explicit linking
    path('dashboard/', views.CompanyDashboardView.as_view(), name='dashboard_alias'),
    
    path('set-tenant/<int:empresa_id>/', views.set_tenant, name='set_tenant'),
    path('clear-tenant/', views.clear_tenant, name='clear_tenant'),
]

