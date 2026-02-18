
"""
Tests de integración para el flujo de UI: Crear Empresa -> Subir Certificado.
"""
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from unittest.mock import patch, MagicMock
from apps.companies.models import Empresa, Membresia
from apps.fiscal.models import CfdiCertificate

User = get_user_model()

class OnboardingFlowTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',  # Required by AbstractUser
            email='test@example.com', 
            password='password123',
            first_name='Test',
            last_name='User'
        )
        self.client.login(email='test@example.com', password='password123')
        
    def test_create_company_flow(self):
        """
        Prueba el flujo completo:
        1. Acceder a formulario de crear empresa
        2. Enviar formulario -> Crea Empresa y Membresía Admin
        3. Redirección a Subir Certificado
        4. Subir Certificado (Mocked) -> Crea CfdiCertificate
        5. Redirección a Dashboard
        """
        
        # 1. GET Create View
        response = self.client.get(reverse('companies:crear_empresa'))
        self.assertEqual(response.status_code, 200)
        
        # 2. POST Create Company
        company_data = {
            'nombre': 'Mi Empresa SAS',
            'rfc': 'MEM101010TST',
            'codigo_postal': '06600'
        }
        response = self.client.post(reverse('companies:crear_empresa'), company_data)
        
        # Verificar creación de empresa
        empresa = Empresa.objects.get(rfc='MEM101010TST')
        self.assertEqual(empresa.nombre, 'Mi Empresa SAS')
        
        # Verificar Membresía Admin
        membresia = Membresia.objects.get(usuario=self.user, empresa=empresa)
        self.assertTrue(membresia.is_admin())
        self.assertTrue(membresia.is_active)
        
        # Verificar Sesión
        self.assertEqual(self.client.session.get('active_empresa_id'), empresa.id)
        
        
        
        # 3. Verificar Redirección a Fiscal Dashboard (antes Subir Certificado)
        self.assertRedirects(response, reverse('fiscal:dashboard'))
        
        # 4. POST Upload Certificate (Mocked validation)
        
        # Mock de validate_certificate_key_pair para retornar éxito
        mock_cert_data = {
            'serial_number': '00001000000500000000',
            'rfc': 'MEM101010TST',
            'valid_from': '2023-01-01',
            'valid_to': '2027-01-01',
            'tipo': 'CSD' # Simulamos detección CSD
        }
        
        with patch('apps.fiscal.forms.validate_certificate_key_pair', return_value=mock_cert_data) as mock_validate:
            # Archivos dummy
            cer_file = SimpleUploadedFile("test.cer", b"dummy_cer_content")
            key_file = SimpleUploadedFile("test.key", b"dummy_key_content")
            
            # POST al endpoint HTMX de carga CSD
            upload_data = {
                'csd-archivo_cer': cer_file,
                'csd-archivo_key': key_file,
                'csd-contrasena': 'secure_password',
            }
            
            # Nuevo endpoint HTMX para CSD
            response = self.client.post(reverse('fiscal:upload_csd'), upload_data)
            
            # Verificar llamada al validador
            self.assertTrue(mock_validate.called)
            
            # Verificar creación de certificado
            cert = CfdiCertificate.objects.get(company=empresa)
            self.assertEqual(cert.serial_number, '00001000000500000000')
            self.assertEqual(cert.rfc, 'MEM101010TST')
            self.assertEqual(cert.tipo, 'CSD')
            
            # Verificar que la empresa ahora tiene el certificado vinculado
            empresa.refresh_from_db()
            self.assertEqual(empresa.certificate, cert)
            
            # En éxito, HTMX endpoint retorna HX-Refresh header (status 200)
            self.assertEqual(response.status_code, 200)
            self.assertIn('HX-Refresh', response.headers)

