from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0024_link_remote_to_inhouse_employee'),
    ]

    operations = [
        # Add tcr_id to Employee
        migrations.AddField(
            model_name='employee',
            name='tcr_id',
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text='Unique company employee ID (e.g. TCR1000224). Same ID links in-house and remote records for the same person.',
                max_length=20,
                null=True,
                unique=True,
                validators=[django.core.validators.RegexValidator(
                    regex='^TCR\\d+$',
                    message='TCR ID must be in the format TCR followed by digits (e.g. TCR1000224).'
                )],
            ),
        ),
        # Add tcr_id to RemoteEmployee
        migrations.AddField(
            model_name='remoteemployee',
            name='tcr_id',
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text='Unique company employee ID (e.g. TCR1000224). Same ID links in-house and remote records for the same person.',
                max_length=20,
                null=True,
                unique=True,
                validators=[django.core.validators.RegexValidator(
                    regex='^TCR\\d+$',
                    message='TCR ID must be in the format TCR followed by digits (e.g. TCR1000224).'
                )],
            ),
        ),
        # Remove the linked_employee FK from RemoteEmployee
        migrations.RemoveField(
            model_name='remoteemployee',
            name='linked_employee',
        ),
    ]
