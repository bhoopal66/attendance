"""
Migration 0045 — Employer Cost Setup (Phase 6)

Additive only: creates the EmployerCostSetup table and its two indexes.
No existing data is altered or removed.

Depends on 0044 (SalaryStructure).
"""

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0044_salary_structure'),
    ]

    operations = [
        migrations.CreateModel(
            name='EmployerCostSetup',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('effective_from', models.DateField(help_text='Date from which this cost setup is effective')),
                ('manpower_monthly_fee', models.DecimalField(
                    decimal_places=2, default=0, max_digits=10,
                    help_text='Agency / manpower company monthly fee')),
                ('visa_amortisation_monthly', models.DecimalField(
                    decimal_places=2, default=0, max_digits=10,
                    help_text='Visa cost spread over visa validity period (monthly)')),
                ('visa_status_change_amortisation', models.DecimalField(
                    decimal_places=2, default=0, max_digits=10,
                    help_text='Status change / transfer cost amortised monthly')),
                ('medical_insurance_monthly', models.DecimalField(
                    decimal_places=2, default=0, max_digits=10,
                    help_text='Medical / health insurance monthly premium')),
                ('eos_provision_monthly', models.DecimalField(
                    decimal_places=2, default=0, max_digits=10,
                    help_text='End-of-service gratuity provision (monthly accrual)')),
                ('leave_salary_provision_monthly', models.DecimalField(
                    decimal_places=2, default=0, max_digits=10,
                    help_text='Leave salary provision accrued monthly')),
                ('air_ticket_provision_monthly', models.DecimalField(
                    decimal_places=2, default=0, max_digits=10,
                    help_text='Annual return air ticket cost spread monthly')),
                ('recruitment_cost_allocation', models.DecimalField(
                    decimal_places=2, default=0, max_digits=10,
                    help_text='Recruitment / placement fee amortised monthly')),
                ('other_cost_monthly', models.DecimalField(
                    decimal_places=2, default=0, max_digits=10,
                    help_text='Any other employer cost not covered above')),
                ('notes', models.TextField(
                    blank=True,
                    help_text='Reason for this revision or any additional context')),
                ('created_by', models.CharField(
                    max_length=150,
                    help_text='Username of the admin who created this record')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('employee', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='cost_setups',
                    to='attendance.employee',
                    help_text='In-house employee (leave blank for remote)')),
                ('remote_employee', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='cost_setups',
                    to='attendance.remoteemployee',
                    help_text='Remote employee (leave blank for in-house)')),
            ],
            options={
                'verbose_name': 'Employer Cost Setup',
                'verbose_name_plural': 'Employer Cost Setups',
                'ordering': ['-effective_from', '-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='employercostsetup',
            index=models.Index(
                fields=['employee', '-effective_from'],
                name='att__ecost_emp_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='employercostsetup',
            index=models.Index(
                fields=['remote_employee', '-effective_from'],
                name='att__ecost_remp_idx',
            ),
        ),
    ]
