from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0022_special_shift_period'),
        ('payroll', '0003_bank_banksubmission'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='banksubmission',
            constraint=models.UniqueConstraint(
                condition=models.Q(employee__isnull=False),
                fields=('employee', 'bank', 'year', 'month'),
                name='unique_inhouse_bank_month',
            ),
        ),
        migrations.AddConstraint(
            model_name='banksubmission',
            constraint=models.UniqueConstraint(
                condition=models.Q(remote_employee__isnull=False),
                fields=('remote_employee', 'bank', 'year', 'month'),
                name='unique_remote_bank_month',
            ),
        ),
    ]
