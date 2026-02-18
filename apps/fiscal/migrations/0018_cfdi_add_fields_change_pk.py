# Custom migration to change CfdiDocument primary key from uuid to id
# and add new fields

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('fiscal', '0017_add_weekly_sync_days_range'),
    ]

    operations = [
        # Step 1: Add all new fields first (they don't depend on PK)
        migrations.AddField(
            model_name='cfdidocument',
            name='serie',
            field=models.CharField(blank=True, max_length=25, null=True, verbose_name='Serie'),
        ),
        migrations.AddField(
            model_name='cfdidocument',
            name='folio',
            field=models.CharField(blank=True, max_length=40, null=True, verbose_name='Folio'),
        ),
        migrations.AddField(
            model_name='cfdidocument',
            name='nombre_emisor',
            field=models.CharField(blank=True, max_length=300, null=True, verbose_name='Nombre Emisor'),
        ),
        migrations.AddField(
            model_name='cfdidocument',
            name='regimen_fiscal_emisor',
            field=models.CharField(blank=True, max_length=10, null=True, verbose_name='Régimen Fiscal Emisor'),
        ),
        migrations.AddField(
            model_name='cfdidocument',
            name='nombre_receptor',
            field=models.CharField(blank=True, max_length=300, null=True, verbose_name='Nombre Receptor'),
        ),
        migrations.AddField(
            model_name='cfdidocument',
            name='regimen_fiscal_receptor',
            field=models.CharField(blank=True, max_length=10, null=True, verbose_name='Régimen Fiscal Receptor'),
        ),
        migrations.AddField(
            model_name='cfdidocument',
            name='domicilio_fiscal_receptor',
            field=models.CharField(blank=True, max_length=10, null=True, verbose_name='CP Receptor'),
        ),
        migrations.AddField(
            model_name='cfdidocument',
            name='subtotal',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=18, verbose_name='Subtotal'),
        ),
        migrations.AddField(
            model_name='cfdidocument',
            name='descuento',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=18, null=True, verbose_name='Descuento'),
        ),
        migrations.AddField(
            model_name='cfdidocument',
            name='fecha_timbrado',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Fecha Timbrado'),
        ),
        migrations.AddField(
            model_name='cfdidocument',
            name='no_certificado_sat',
            field=models.CharField(blank=True, max_length=50, null=True, verbose_name='No. Certificado SAT'),
        ),
        migrations.AddField(
            model_name='cfdidocument',
            name='cfdi_state',
            field=models.CharField(
                choices=[
                    ('draft', 'Borrador'),
                    ('sent', 'Firmado/Enviado'),
                    ('cancel_requested', 'Cancelación Solicitada'),
                    ('cancel', 'Cancelado'),
                    ('received', 'Recibido'),
                    ('global_sent', 'Global Firmado'),
                    ('global_cancel', 'Global Cancelado'),
                ],
                default='received',
                help_text='Estado del ciclo de vida del CFDI',
                max_length=20,
                verbose_name='Estado CFDI'
            ),
        ),
        migrations.AddField(
            model_name='cfdidocument',
            name='creado_en_sistema',
            field=models.BooleanField(
                default=False,
                help_text='True si fue creado/vinculado en sistema contable',
                verbose_name='Creado en Sistema'
            ),
        ),

        # Step 2: Add index for cfdi_state
        migrations.AddIndex(
            model_name='cfdidocument',
            index=models.Index(fields=['cfdi_state'], name='fiscal_cfdi_cfdi_st_ced4ee_idx'),
        ),

        # Step 3: Raw SQL to change primary key from uuid to id
        # This is PostgreSQL-specific
        migrations.RunSQL(
            sql=[
                # Drop FK constraints that reference the old PK
                'ALTER TABLE fiscal_cfdistatecheck DROP CONSTRAINT IF EXISTS fiscal_cfdistatechec_document_id_1f08f32c_fk_fiscal_cf;',
                'ALTER TABLE fiscal_cfdidocument DROP CONSTRAINT IF EXISTS fiscal_cfdidocument_current_state_check_id_fk;',

                # Drop the existing primary key constraint
                'ALTER TABLE fiscal_cfdidocument DROP CONSTRAINT fiscal_cfdidocument_pkey CASCADE;',

                # Add id column with BIGSERIAL (auto-incrementing)
                'ALTER TABLE fiscal_cfdidocument ADD COLUMN id BIGSERIAL;',

                # Set id as the new primary key
                'ALTER TABLE fiscal_cfdidocument ADD PRIMARY KEY (id);',

                # Add db_index to uuid (if not already there)
                'CREATE INDEX IF NOT EXISTS fiscal_cfdidocument_uuid_idx ON fiscal_cfdidocument (uuid);',

                # Recreate FK constraint on fiscal_cfdistatecheck to reference new PK
                # First we need to add a document_id column that references the new id
                'ALTER TABLE fiscal_cfdistatecheck ADD COLUMN IF NOT EXISTS document_id_new BIGINT;',

                # Update the new column with correct values by joining on uuid
                '''UPDATE fiscal_cfdistatecheck s
                   SET document_id_new = d.id
                   FROM fiscal_cfdidocument d
                   WHERE s.document_id = d.uuid;''',

                # Drop the old document_id column (uuid type)
                'ALTER TABLE fiscal_cfdistatecheck DROP COLUMN document_id;',

                # Rename the new column
                'ALTER TABLE fiscal_cfdistatecheck RENAME COLUMN document_id_new TO document_id;',

                # Add NOT NULL constraint
                'ALTER TABLE fiscal_cfdistatecheck ALTER COLUMN document_id SET NOT NULL;',

                # Add FK constraint
                '''ALTER TABLE fiscal_cfdistatecheck
                   ADD CONSTRAINT fiscal_cfdistatecheck_document_id_fk
                   FOREIGN KEY (document_id) REFERENCES fiscal_cfdidocument(id) ON DELETE CASCADE;''',

                # Create index on document_id
                'CREATE INDEX IF NOT EXISTS fiscal_cfdistatecheck_document_id_idx ON fiscal_cfdistatecheck (document_id);',
            ],
            reverse_sql=[
                # This is a complex reverse - for safety just fail
                'SELECT 1;',  # No-op - manual intervention required for reverse
            ],
        ),
    ]
