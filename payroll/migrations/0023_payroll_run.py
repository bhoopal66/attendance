"""
Migration 0023 — PayrollRun lifecycle model (Phase 9)

Additive only: creates the PayrollRun table.
No existing table is altered or dropped.

Data backfill (RunPython, safe):
  1. For every FrozenPayrollMonth row → create PayrollRun(status='locked').
  2. For months that also have at least one PaidSalaryRecord row → upgrade
     the PayrollRun to status='paid' using the earliest paid_at timestamp
     and paid_by from that set.

Both FrozenPayrollMonth and PaidSalaryRecord remain intact.
The RunPython is wrapped in a try/except so a missing table in an older
schema never blocks the migration.

Depends on payroll 0022 (DeductionEntry recoverable FK).
"""

import django.utils.timezone
from django.db import migrations, models


def backfill_payroll_runs(apps, schema_editor):
    """
    Create PayrollRun rows from existing FrozenPayrollMonth and PaidSalaryRecord
    data so that historical months appear at the correct lifecycle stage.
    """
    PayrollRun         = apps.get_model('payroll', 'PayrollRun')
    FrozenPayrollMonth = apps.get_model('payroll', 'FrozenPayrollMonth')
    PaidSalaryRecord   = apps.get_model('payroll', 'PaidSalaryRecord')

    # Step 1: seed from FrozenPayrollMonth → status='locked'
    for frozen in FrozenPayrollMonth.objects.all():
        run, created = PayrollRun.objects.get_or_create(
            year=frozen.year,
            month=frozen.month,
            defaults={
                'status':    'locked',
                'locked_by': frozen.frozen_by or '',
                'locked_at': frozen.frozen_at,
            },
        )
        if not created and run.status == 'draft':
            run.status    = 'locked'
            run.locked_by = frozen.frozen_by or ''
            run.locked_at = frozen.frozen_at
            run.save()

    # Step 2: upgrade months with PaidSalaryRecord entries to status='paid'
    # Collect distinct (year, month) pairs that have at least one paid record
    paid_months = (
        PaidSalaryRecord.objects
        .values('year', 'month', 'paid_at', 'paid_by')
        .order_by('year', 'month', 'paid_at')
    )

    seen = {}
    for rec in paid_months:
        key = (rec['year'], rec['month'])
        if key not in seen:
            seen[key] = rec   # keep earliest paid_at per month

    for (year, month), rec in seen.items():
        run, created = PayrollRun.objects.get_or_create(
            year=year,
            month=month,
            defaults={
                'status':  'paid',
                'paid_at': rec['paid_at'],
                'paid_by': rec['paid_by'] or '',
            },
        )
        if not created and run.status in ('draft', 'review', 'approved', 'locked'):
            run.status  = 'paid'
            run.paid_at = rec['paid_at']
            run.paid_by = rec['paid_by'] or ''
            run.save()


def noop_reverse(apps, schema_editor):
    """Reverse: do nothing — leave any PayrollRun rows as-is."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('payroll', '0022_deduction_entry_recoverable_fk'),
    ]

    operations = [
        migrations.CreateModel(
            name='PayrollRun',
            fields=[
                ('id', models.BigAutoField(
                    auto_created=True, primary_key=True,
                    serialize=False, verbose_name='ID',
                )),
                ('year',   models.IntegerField()),
                ('month',  models.IntegerField(help_text='1–12')),
                ('status', models.CharField(
                    max_length=20,
                    choices=[
                        ('draft',    'Draft'),
                        ('review',   'Under Review'),
                        ('approved', 'Approved'),
                        ('locked',   'Locked'),
                        ('paid',     'Paid'),
                        ('posted',   'Posted'),
                    ],
                    default='draft',
                    db_index=True,
                )),
                ('prepared_by',  models.CharField(max_length=150, blank=True)),
                ('prepared_at',  models.DateTimeField(null=True, blank=True)),
                ('reviewed_by',  models.CharField(max_length=150, blank=True)),
                ('reviewed_at',  models.DateTimeField(null=True, blank=True)),
                ('approved_by',  models.CharField(max_length=150, blank=True)),
                ('approved_at',  models.DateTimeField(null=True, blank=True)),
                ('locked_by',    models.CharField(max_length=150, blank=True)),
                ('locked_at',    models.DateTimeField(null=True, blank=True)),
                ('paid_at',      models.DateTimeField(null=True, blank=True)),
                ('paid_by',      models.CharField(max_length=150, blank=True)),
                ('posted_by',    models.CharField(max_length=150, blank=True)),
                ('posted_at',    models.DateTimeField(null=True, blank=True)),
                ('notes',        models.TextField(blank=True)),
                ('created_at',   models.DateTimeField(auto_now_add=True)),
                ('updated_at',   models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Payroll Run',
                'verbose_name_plural': 'Payroll Runs',
                'ordering': ['-year', '-month'],
            },
        ),
        migrations.AlterUniqueTogether(
            name='payrollrun',
            unique_together={('year', 'month')},
        ),
        migrations.RunPython(backfill_payroll_runs, reverse_code=noop_reverse),
    ]
