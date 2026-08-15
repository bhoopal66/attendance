"""Paid Holidays — monthly declaration and per-employee awards.

Two new tables. Touches nothing existing: the additions a confirmed
declaration creates are ordinary `DeductionEntry` rows, written through the
normal path, so no money-bearing column is added or altered.

Seeds no deduction type. The `paid_holiday` type is created from the Deduction
Types page (it already exists in production), and `services_paid_holidays`
refuses to generate anything if it is missing rather than inventing a category.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0049_rename_attendance__employm_idx_attendance__employe_d26f67_idx_and_more'),
        ('payroll', '0031_deduction_rule'),
    ]

    operations = [
        migrations.CreateModel(
            name='PaidHolidayDeclaration',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('year', models.IntegerField()),
                ('month', models.IntegerField(help_text='1-12')),
                ('dates', models.JSONField(blank=True, default=list, help_text='ISO dates being paid, e.g. ["2026-01-01"]. An empty list is a valid declaration meaning no paid holidays this month.')),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('confirmed', 'Confirmed'), ('withdrawn', 'Withdrawn')], db_index=True, default='draft', max_length=20)),
                ('note', models.TextField(blank=True)),
                ('created_by', models.CharField(blank=True, max_length=150)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('confirmed_by', models.CharField(blank=True, max_length=150)),
                ('confirmed_at', models.DateTimeField(blank=True, null=True)),
                ('withdrawn_by', models.CharField(blank=True, max_length=150)),
                ('withdrawn_at', models.DateTimeField(blank=True, null=True)),
                ('withdrawn_reason', models.CharField(blank=True, max_length=255)),
            ],
            options={
                'verbose_name': 'Paid Holiday Declaration',
                'verbose_name_plural': 'Paid Holiday Declarations',
                'ordering': ['-year', '-month'],
                'unique_together': {('year', 'month')},
            },
        ),
        migrations.CreateModel(
            name='PaidHolidayAward',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('days', models.PositiveIntegerField(default=0)),
                ('gross_used', models.DecimalField(decimal_places=2, default=0, help_text="The employee's gross for the month, as the daily rate was derived from it.", max_digits=12)),
                ('period_days', models.PositiveIntegerField(default=0, help_text="Days in that employee's pay period - the divisor.")),
                ('daily_rate', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('amount', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('currency', models.CharField(default='AED', max_length=3)),
                ('skipped', models.BooleanField(default=False)),
                ('skip_reason', models.CharField(blank=True, max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('declaration', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='awards', to='payroll.paidholidaydeclaration')),
                ('deduction_entry', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='paid_holiday_awards', to='payroll.deductionentry')),
                ('employee', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='paid_holiday_awards', to='attendance.employee')),
                ('remote_employee', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='paid_holiday_awards', to='attendance.remoteemployee')),
            ],
            options={'ordering': ['declaration_id', 'id']},
        ),
    ]
