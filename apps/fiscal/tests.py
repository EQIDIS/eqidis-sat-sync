"""
Tests para CfdiDocument.update_state() y auditoría.
"""
from django.test import TestCase
from django.utils import timezone
from apps.fiscal.models import CfdiDocument, CfdiStateCheck, CfdiParserVersion
from apps.companies.models import Empresa
from apps.core.models import AuditLog
from apps.users.models import CustomUser
import uuid


class CfdiDocumentUpdateStateTest(TestCase):
    """
    Tests para el método update_state() de CfdiDocument.
    
    Verifica que:
    1. Se cree registro en CfdiStateCheck
    2. Se actualice current_state_check
    3. Se registre en AuditLog si hubo cambio
   4. Los campos de cache se actualicen correctamente
    """
    
    def setUp(self):
        """Configura datos de prueba."""
        # Crear usuario de prueba
        self.user = CustomUser.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
        # Crear empresa de prueba
        self.empresa = Empresa.objects.create(
            nombre='Empresa Test',
            rfc='TST010101AAA'
        )
        
        # Crear versión de parser
        self.parser_version = CfdiParserVersion.objects.create(
            cfdi_version='4.0',
            xsd_version='4.0',
            xsd_hash='test_hash',
            sat_release_date='2022-01-01',
            valid_from='2022-01-01',
            is_active=True
        )
        
        # Crear CFDI de prueba
        self.cfdi = CfdiDocument.objects.create(
            uuid=uuid.uuid4(),
            company=self.empresa,
            rfc_emisor='EMI010101AAA',
            rfc_receptor='REC010101AAA',
            tipo_cfdi='I',
            total=1000.00,
            moneda='MXN',
            fecha_emision=timezone.now(),
            estado_sat='Vigente',
            parser_version=self.parser_version
        )
    
    def test_update_state_creates_state_check(self):
        """Verifica que update_state cree registro en CfdiStateCheck."""
        initial_count = CfdiStateCheck.objects.count()
        
        self.cfdi.update_state(
            nuevo_estado_sat='Cancelado',
            source='uuid_check',
            user=self.user
        )
        
        # Debe haber un nuevo CfdiStateCheck
        self.assertEqual(CfdiStateCheck.objects.count(), initial_count + 1)
        
        # Verificar el registro creado
        state_check = CfdiStateCheck.objects.latest('checked_at')
        self.assertEqual(state_check.document, self.cfdi)
        self.assertEqual(state_check.estado_anterior, 'Vigente')
        self.assertEqual(state_check.estado_sat, 'Cancelado')
        self.assertTrue(state_check.es_cambio)
        self.assertEqual(state_check.source, 'uuid_check')
    
    def test_update_state_updates_current_state_check(self):
        """Verifica que se actualice current_state_check FK."""
        self.assertIsNone(self.cfdi.current_state_check)
        
        self.cfdi.update_state(
            nuevo_estado_sat='Cancelado',
            source='manual'
        )
        
        # current_state_check debe apuntar al último registro
        self.assertIsNotNone(self.cfdi.current_state_check)
        self.assertEqual(self.cfdi.current_state_check.estado_sat, 'Cancelado')
    
    def test_update_state_creates_audit_log_on_change(self):
        """Verifica que se cree AuditLog cuando hay cambio de estado."""
        initial_count = AuditLog.objects.count()
        
        self.cfdi.update_state(
            nuevo_estado_sat='Cancelado',
            source='uuid_check',
            user=self.user
        )
        
        # Debe haber un nuevo AuditLog
        self.assertEqual(AuditLog.objects.count(), initial_count + 1)
        
        # Verificar el log creado
        audit_log = AuditLog.objects.latest('created_at')
        self.assertEqual(audit_log.empresa, self.empresa)
        self.assertEqual(audit_log.entity_type, 'CfdiDocument')
        self.assertEqual(audit_log.entity_id, str(self.cfdi.uuid))
        self.assertEqual(audit_log.action, 'status_change')
        self.assertEqual(audit_log.user, self.user)
