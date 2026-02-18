from django import forms
from .models import Empresa

class EmpresaForm(forms.ModelForm):
    """
    Formulario para crear y editar entidades Empresa (Tenants).
    Validaciones básicas de RFC y campos requeridos.
    """
    class Meta:
        model = Empresa
        fields = [
            'nombre',
            'razon_social',
            'rfc',
            'regimen_fiscal',
            'codigo_postal',
            'direccion',
            'telefono',
            'email',
            'logo'
        ]
        widgets = {
            'nombre': forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
            'razon_social': forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
            'rfc': forms.TextInput(attrs={'class': 'input input-bordered w-full uppercase', 'maxlength': '13'}),
            'regimen_fiscal': forms.Select(attrs={'class': 'select select-bordered w-full'}),
            'codigo_postal': forms.TextInput(attrs={'class': 'input input-bordered w-full', 'maxlength': '5'}),
            'direccion': forms.Textarea(attrs={'class': 'textarea textarea-bordered w-full', 'rows': 3}),
            'telefono': forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
            'email': forms.EmailInput(attrs={'class': 'input input-bordered w-full'}),
            'logo': forms.FileInput(attrs={'class': 'file-input file-input-bordered w-full'}),
        }
        help_texts = {
            'rfc': 'Para Personas Morales use 12 caracteres, para Físicas 13.',
            'regimen_fiscal': 'Seleccione el régimen principal ante el SAT.',
        }

    def clean_rfc(self):
        """Valida formato básico de RFC y lo convierte a mayúsculas."""
        rfc = self.cleaned_data.get('rfc')
        if rfc:
            rfc = rfc.upper().strip()
            if len(rfc) < 12 or len(rfc) > 13:
                raise forms.ValidationError("El RFC debe tener 12 o 13 caracteres.")
            # TODO: Agregar validación regex más estricta si se requiere
        return rfc
