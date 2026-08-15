"""Payroll re-run — lets a month be re-opened and recalculated.

Four additive fields on PayrollRun. No data is migrated and nothing existing
is altered; every current run reads as reopened_count = 0, which is true.

The destructive part of a re-run is not this migration — it is
`services_payroll_rerun.reopen_run`, which deletes that month's
PaidSalaryRecord and FrozenPayrollMonth rows so the month recomputes from live
data. That behaviour was chosen explicitly (delete and start clean, any month,
any stage) in preference to versioning the snapshots. The summary of what was
destroyed is written to AuditLog first, because once the snapshot is gone it is
the only remaining answer to "what did this month originally pay?".
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payroll', '0033_sunday_entitlement'),
    ]

    operations = [
        migrations.AddField(
            model_name='payrollrun',
            name='reopened_count',
            field=models.PositiveIntegerField(default=0, help_text='How many times this month has been re-opened and re-run.'),
        ),
        migrations.AddField(
            model_name='payrollrun',
            name='reopened_by',
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.AddField(
            model_name='payrollrun',
            name='reopened_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='payrollrun',
            name='reopen_reason',
            field=models.TextField(blank=True, help_text='Why the month was last re-opened. Required — re-opening discards the locked figures, and that needs an explanation attached to it.'),
        ),
    ]
