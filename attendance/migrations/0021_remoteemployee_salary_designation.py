from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0020_add_unique_constraint_attendance_record'),
    ]

    operations = [
        migrations.AddField(
            model_name='remoteemployee',
            name='salary',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name='remoteemployee',
            name='designation',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
    ]
