from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0025_tcr_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='employee',
            name='currency',
            field=models.CharField(
                choices=[('AED', 'AED'), ('INR', 'INR')],
                default='AED',
                help_text='Currency for salary payment',
                max_length=3,
            ),
        ),
        migrations.AddField(
            model_name='remoteemployee',
            name='currency',
            field=models.CharField(
                choices=[('AED', 'AED'), ('INR', 'INR')],
                default='AED',
                help_text='Currency for salary payment',
                max_length=3,
            ),
        ),
    ]
