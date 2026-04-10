import django.db.models.deletion
from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0026_employee_currency'),
        ('payroll', '0011_bank_inr_per_account_charge'),
    ]

    operations = [
        migrations.CreateModel(
            name='DeductionCarryover',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('from_year', models.IntegerField()),
                ('from_month', models.IntegerField()),
                ('to_year', models.IntegerField()),
                ('to_month', models.IntegerField()),
                ('overflow_amount', models.DecimalField(decimal_places=2, max_digits=10)),
                ('applied_amount', models.DecimalField(decimal_places=2, default=Decimal('0'), max_digits=10)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('employee', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='deduction_carryovers',
                    to='attendance.employee',
                )),
                ('remote_employee', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='deduction_carryovers',
                    to='attendance.remoteemployee',
                )),
            ],
        ),
        migrations.AddConstraint(
            model_name='deductioncarryover',
            constraint=models.UniqueConstraint(
                condition=models.Q(employee__isnull=False),
                fields=['employee', 'from_year', 'from_month'],
                name='unique_inhouse_carryover_month',
            ),
        ),
        migrations.AddConstraint(
            model_name='deductioncarryover',
            constraint=models.UniqueConstraint(
                condition=models.Q(remote_employee__isnull=False),
                fields=['remote_employee', 'from_year', 'from_month'],
                name='unique_remote_carryover_month',
            ),
        ),
    ]
