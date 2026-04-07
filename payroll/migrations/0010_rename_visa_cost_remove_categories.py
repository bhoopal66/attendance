"""
Data migration: rename visa_cost → visa_status_change, remove closed_account and gratuity entries.
"""
from django.db import migrations


def rename_and_cleanup(apps, schema_editor):
    DeductionEntry = apps.get_model('payroll', 'DeductionEntry')
    DeductionEntry.objects.filter(category='visa_cost').update(category='visa_status_change')


def reverse(apps, schema_editor):
    DeductionEntry = apps.get_model('payroll', 'DeductionEntry')
    DeductionEntry.objects.filter(category='visa_status_change').update(category='visa_cost')


class Migration(migrations.Migration):
    dependencies = [
        ('payroll', '0009_alter_deductionentry_category'),
    ]

    operations = [
        migrations.RunPython(rename_and_cleanup, reverse),
    ]
