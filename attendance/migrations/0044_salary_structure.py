"""
Phase 5 — Salary Structure

Creates the SalaryStructure model and back-fills one approved row per employee
that already has a salary value set.  All other fields stay untouched.

Backfill logic
--------------
  basic          = employee.salary  (all gross in basic component)
  housing        = 0
  transport      = 0
  phone          = 0
  other_allowance= 0
  currency       = employee.currency (or 'AED')
  effective_from = employee.joining_date  (or 2025-01-01 if null)
  status         = 'approved'
  created_by     = 'system_backfill'

Only employees with a non-null salary are backfilled — no placeholder rows for
employees without salary data.

Dependencies
------------
  - 0043_employment_history  (Phase 4)
"""

from django.db import migrations, models
import django.db.models.deletion
import datetime


def backfill_salary_structures(apps, schema_editor):
    Employee        = apps.get_model('attendance', 'Employee')
    SalaryStructure = apps.get_model('attendance', 'SalaryStructure')

    fallback_date = datetime.date(2025, 1, 1)
    rows = []

    for emp in Employee.objects.filter(salary__isnull=False).iterator():
        rows.append(SalaryStructure(
            employee=emp,
            effective_from=emp.joining_date or fallback_date,
            basic=emp.salary,
            housing=0,
            transport=0,
            phone=0,
            other_allowance=0,
            currency=emp.currency or 'AED',
            revision_reason='',
            status='approved',
            created_by='system_backfill',
        ))

    if rows:
        SalaryStructure.objects.bulk_create(rows)


def reverse_backfill(apps, schema_editor):
    """Delete only system_backfill rows to safely reverse."""
    SalaryStructure = apps.get_model('attendance', 'SalaryStructure')
    SalaryStructure.objects.filter(created_by='system_backfill').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0043_employment_history'),
    ]

    operations = [
        migrations.CreateModel(
            name='SalaryStructure',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('employee', models.ForeignKey(
                    help_text='In-house employee this salary structure belongs to',
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='salary_structures',
                    to='attendance.employee',
                )),
                ('effective_from', models.DateField(
                    help_text='Date from which this salary structure is effective',
                )),
                ('basic', models.DecimalField(
                    decimal_places=2, default=0, max_digits=12,
                    help_text='Basic / base pay component',
                )),
                ('housing', models.DecimalField(
                    decimal_places=2, default=0, max_digits=12,
                    help_text='Housing allowance',
                )),
                ('transport', models.DecimalField(
                    decimal_places=2, default=0, max_digits=12,
                    help_text='Transport allowance',
                )),
                ('phone', models.DecimalField(
                    decimal_places=2, default=0, max_digits=12,
                    help_text='Phone / communication allowance',
                )),
                ('other_allowance', models.DecimalField(
                    decimal_places=2, default=0, max_digits=12,
                    help_text='Any other allowance not covered above',
                )),
                ('currency', models.CharField(
                    default='AED', max_length=3,
                    help_text='ISO currency code (e.g. AED, INR, NPR)',
                )),
                ('revision_reason', models.TextField(
                    blank=True,
                    help_text='Business reason for this salary revision',
                )),
                ('status', models.CharField(
                    choices=[('approved', 'Approved'), ('superseded', 'Superseded')],
                    db_index=True, default='approved', max_length=20,
                    help_text="'approved' = current; 'superseded' = replaced by a newer revision",
                )),
                ('created_by', models.CharField(
                    max_length=150,
                    help_text='Username of the admin who created this revision',
                )),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'verbose_name': 'Salary Structure',
                'verbose_name_plural': 'Salary Structures',
                'ordering': ['-effective_from', '-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='salarystructure',
            index=models.Index(
                fields=['employee', '-effective_from'],
                name='attendance__salary_emp_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='salarystructure',
            index=models.Index(
                fields=['status'],
                name='attendance__salary_status_idx',
            ),
        ),
        migrations.RunPython(backfill_salary_structures, reverse_code=reverse_backfill),
    ]
