"""
Tests para validaciones de reglas de negocio SAT - PUE/PPD.
"""
from django.test import TestCase
from django.core.exceptions import ValidationError
from django.utils import timezone
from apps.fiscal.models import CfdiDocument, CfdiParserVersion, FormaPago
from apps.companies.models import Empresa
import uuid


class CfdiPuePpdValidationTest(TestCase):
    """
    Tests para validar reglas SAT de metodo_pago vs forma_pago.
    
    REGLAS CFDI 4.0:
    - PUE (Pago en Una Exhibición): forma_pago debe ser específica (NO 99)
    - PPD (Pago en Parcialidades): forma_pago puede ser 99 (Por definir)
    """
    
    def setUp(self):
        """Configura datos de prueba."""
        # Crear empresa
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
        
        # Crear formas de pago
        self.forma_pago_99 = FormaPago.objects.create(
            clave='99',
            descripcion='Por definir',
            is_active=True
        )
        
        self.forma_pago_03 = FormaPago.objects.create(
            clave='03',
            descripcion='Transferencia electrónica de fondos',
            is_active=True
        )
    
    def test_pue_with_forma_pago_99_raises_error(self):
        """PUE con forma de pago '99' debe lanzar ValidationError."""
        cfdi = CfdiDocument(
            uuid=uuid.uuid4(),
            company=self.empresa,
            rfc_emisor='EMI010101AAA',
            rfc_receptor='REC010101AAA',
            tipo_cfdi='I',
            metodo_pago='PUE',  # Pago en Una Exhibición
            forma_pago=self.forma_pago_99,  # '99' - NO VÁLIDO para PUE
            total=1000.00,
            moneda='MXN',
            fecha_emision=timezone.now(),
            parser_version=self.parser_version
        )
        
        # Debe lanzar ValidationError
        with self.assertRaises(ValidationError) as context:
            cfdi.save()
        
        # Verificar mensaje de error
        self.assertIn('forma_pago', context.exception.message_dict)
        self.assertIn('PUE', str(context.exception))
        self.assertIn('99', str(context.exception))
    
    def test_pue_with_specific_forma_pago_is_valid(self):
        """PUE con forma de pago específica (03) debe ser válido."""
        cfdi = CfdiDocument(
            uuid=uuid.uuid4(),
            company=self.empresa,
            rfc_emisor='EMI010101AAA',
            rfc_receptor='REC010101AAA',
            tipo_cfdi='I',
            metodo_pago='PUE',  # Pago en Una Exhibición
            forma_pago=self.forma_pago_03,  # '03' - VÁLIDO para PUE
            total=1000.00,
            moneda='MXN',
            fecha_emision=timezone.now(),
            parser_version=self.parser_version
        )
        
        # No debe lanzar error
        try:
            cfdi.save()
            self.assertTrue(True, "PUE con forma específica es válido")
        except ValidationError:
            self.fail("PUE con forma_pago específica no debería lanzar error")
    
    def test_ppd_with_forma_pago_99_is_valid(self):
        """PPD con forma de pago '99' debe ser válido."""
        cfdi = CfdiDocument(
            uuid=uuid.uuid4(),
            company=self.empresa,
            rfc_emisor='EMI010101AAA',
            rfc_receptor='REC010101AAA',
            tipo_cfdi='I',
            metodo_pago='PPD',  # Pago en Parcialidades
            forma_pago=self.forma_pago_99,  # '99' - VÁLIDO para PPD
            total=1000.00,
            moneda='MXN',
            fecha_emision=timezone.now(),
            parser_version=self.parser_version
        )
        
        # No debe lanzar error
        try:
            cfdi.save()
            self.assertTrue(True, "PPD con forma_pago 99 es válido")
        except ValidationError:
            self.fail("PPD con forma_pago 99 no debería lanzar error")
    
    def test_ppd_with_specific_forma_pago_is_valid(self):
        """PPD con forma de pago específica (03) también es válido (aunque inusual)."""
        cfdi = CfdiDocument(
            uuid=uuid.uuid4(),
            company=self.empresa,
            rfc_emisor='EMI010101AAA',
            rfc_receptor='REC010101AAA',
            tipo_cfdi='I',
            metodo_pago='PPD',  # Pago en Parcialidades
            forma_pago=self.forma_pago_03,  # '03' - VÁLIDO (pero inusual para PPD)
            total=1000.00,
            moneda='MXN',
            fecha_emision=timezone.now(),
            parser_version=self.parser_version
        )
        
        # No debe lanzar error (es válido aunque inusual)
        try:
            cfdi.save()
            self.assertTrue(True, "PPD con forma específica es técnicamente válido")
        except ValidationError:
            self.fail("PPD con forma_pago específica no debería lanzar error")
    
    def test_cfdi_without_metodo_pago_skips_validation(self):
        """CFDI sin metodo_pago no ejecuta validación."""
        cfdi = CfdiDocument(
            uuid=uuid.uuid4(),
            company=self.empresa,
            rfc_emisor='EMI010101AAA',
            rfc_receptor='REC010101AAA',
            tipo_cfdi='I',
            # metodo_pago=None  # Sin especificar
            total=1000.00,
            moneda='MXN',
            fecha_emision=timezone.now(),
            parser_version=self.parser_version
        )
        
        # No debe lanzar error
        try:
            cfdi.save()
            self.assertTrue(True, "CFDI sin metodo_pago no lanza error")
        except ValidationError:
            self.fail("CFDI sin metodo_pago no debería ejecutar validación")
