from django.views.generic import TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import FileResponse, Http404
from django.conf import settings
from pathlib import Path


class HomeView(LoginRequiredMixin, TemplateView):
    """
    Panel principal sin empresa requerida.
    Desde aquí el usuario puede:
    - Ver enlaces a configuración global (Odoo, tokens internos, etc.)
    - Ir a flujos que sí requieren empresa (SAT, CFDIs, sincronización), que seguirán validando tenant.
    """
    template_name = "core/home.html"


def favicon_view(request):
    """
    Serve favicon directly from project root `favicon.ico` to avoid requiring it
    to be in the Django `static/` folder.
    """
    favicon_path = Path(settings.BASE_DIR) / "favicon.ico"
    if not favicon_path.exists():
        raise Http404("Favicon not found")
    return FileResponse(favicon_path.open("rb"), content_type="image/x-icon")
