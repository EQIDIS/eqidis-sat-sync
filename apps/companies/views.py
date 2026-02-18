from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.conf import settings
from django_multitenant.utils import unset_current_tenant
from .models import Empresa, Membresia


@login_required
def seleccionar_empresa(request):
    """
    View to display available companies for the current user.
    User can select which company to work with.
    """
    # Unset any current tenant for this view
    unset_current_tenant()
    
    # Get all companies the user has access to
    membresias = request.user.membresias.filter(
        is_active=True
    ).select_related('empresa')
    
    # If user only has one company, auto-select it
    if membresias.count() == 1:
        membresia = membresias.first()
        session_key = getattr(settings, 'TENANT_SESSION_KEY', 'active_empresa_id')
        request.session[session_key] = membresia.empresa.id
        messages.success(request, f'Bienvenido a {membresia.empresa.nombre}')
        return redirect('/')
    
    # If user has no companies, show message
    if not membresias.exists():
        messages.warning(
            request, 
            'No tienes acceso a ninguna empresa. Contacta al administrador.'
        )
    
    context = {
        'membresias': membresias,
    }
    return render(request, 'companies/seleccionar_empresa.html', context)


@login_required
def set_tenant(request, empresa_id):
    """
    View to set the active company in session.
    Called when user clicks on a company in the selector.
    """
    # Verify user has access to this company
    membresia = get_object_or_404(
        Membresia,
        usuario=request.user,
        empresa_id=empresa_id,
        is_active=True
    )
    
    # Set the active company in session
    session_key = getattr(settings, 'TENANT_SESSION_KEY', 'active_empresa_id')
    request.session[session_key] = membresia.empresa.id
    
    messages.success(request, f'Cambiaste a {membresia.empresa.nombre}')
    
    # Redirect to home or the next URL if provided
    next_url = request.GET.get('next', '/')
    return redirect(next_url)


@login_required
def clear_tenant(request):
    """
    View to clear the active company from session.
    Returns user to company selector.
    """
    session_key = getattr(settings, 'TENANT_SESSION_KEY', 'active_empresa_id')
    if session_key in request.session:
        del request.session[session_key]
    
    return redirect('companies:seleccionar_empresa')


from django.views.generic import CreateView, TemplateView
from django.urls import reverse_lazy
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from .forms import EmpresaForm

class CompanyCreateView(LoginRequiredMixin, CreateView):
    """
    Vista para crear una nueva Empresa.
    Automáticamente asigna al usuario creador como Administrador.
    """
    model = Empresa
    form_class = EmpresaForm
    template_name = 'companies/crear_empresa.html'
    success_url = reverse_lazy('fiscal:dashboard') # Flujo: Crear -> Dashboard Fiscal

    def form_valid(self, form):
        with transaction.atomic():
            # 1. Crear la empresa
            self.object = form.save()
            
            # 2. Crear Membresía Admin para el usuario actual
            Membresia.objects.create(
                usuario=self.request.user,
                empresa=self.object,
                rol='admin',
                is_active=True
            )
            
            # 3. Auto-activar la empresa en la sesión
            session_key = getattr(settings, 'TENANT_SESSION_KEY', 'active_empresa_id')
            self.request.session[session_key] = self.object.id
            
            messages.success(self.request, f'Empresa "{self.object.nombre}" creada exitosamente. Eres el Administrador.')
            
            # Log de auditoría (si estuviera en el mismo app, por ahora simple)
            # AuditLog.log(...) 

        return redirect(self.get_success_url())

class CompanyDashboardView(LoginRequiredMixin, TemplateView):
    """
    Dashboard principal de la empresa (Home del Tenant).
    """
    template_name = 'companies/dashboard.html'
    
    def dispatch(self, request, *args, **kwargs):
        # Verificar tenant activo
        session_key = getattr(settings, 'TENANT_SESSION_KEY', 'active_empresa_id')
        empresa_id = request.session.get(session_key)
        
        if not empresa_id:
            return redirect('companies:seleccionar_empresa')
            
        return super().dispatch(request, *args, **kwargs)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        empresa = getattr(self.request, 'empresa', None)
        
        if empresa:
            from apps.fiscal.models import CfdiCertificate
            
            context['csd'] = CfdiCertificate.objects.filter(
                company=empresa, tipo='CSD', status='active'
            ).first()
            
            context['fiel'] = CfdiCertificate.objects.filter(
                company=empresa, tipo='FIEL', status='active'
            ).first()
            
            # Certificados completos si ambos están activos
            context['certs_complete'] = context['csd'] and context['fiel']
        
        return context


