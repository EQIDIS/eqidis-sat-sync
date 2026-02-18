"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import TemplateView


urlpatterns = [
    # Admin
    path('admin/', admin.site.urls),
    
    # Allauth authentication
    path('accounts/', include('allauth.urls')),
    
    # Company management (tenant switching)
    path('', include('apps.companies.urls')),
    
    # Fiscal module (incl. CFDIs y integración Odoo)
    path('fiscal/', include('apps.fiscal.urls')),
]

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0] if settings.STATICFILES_DIRS else settings.STATIC_ROOT)
