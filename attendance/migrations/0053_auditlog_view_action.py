from django.db import migrations, models


class Migration(migrations.Migration):
    """Adds the 'view' action so a sensitive-data read can be recorded.

    Choices-only change: no data is touched, no existing row is reinterpreted.
    """

    dependencies = [
        ('attendance', '0052_compliance_fields'),
    ]

    operations = [
        migrations.AlterField(
            model_name='auditlog',
            name='action',
            field=models.CharField(
                db_index=True, max_length=20,
                choices=[
                    ('create', 'Create'),
                    ('update', 'Update'),
                    ('delete', 'Delete'),
                    ('transition', 'Status Change'),
                    ('view', 'Sensitive Data Viewed'),
                ],
            ),
        ),
    ]
