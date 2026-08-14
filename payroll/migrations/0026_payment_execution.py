"""
Phase E6 — payment execution fields on PaidSalaryRecord.

Adds amount_paid / payment_method / payment_date so a payment can be recorded
as partial, and so the method and value date are captured at the moment of
disbursement.

BACKFILL NOTE (important — these are locked records):
Existing PaidSalaryRecord rows are immutable payroll snapshots. This migration
does NOT alter final_salary, currency, paid_at, paid_by or snapshot on any
existing row. It only populates the new amount_paid column with the value that
was already implicitly true: every pre-existing row was created by a full
"Mark as Paid" action, so amount_paid = final_salary restates an existing fact
rather than changing a locked figure.

payment_method is deliberately left blank on historical rows rather than
guessed — the system genuinely does not know how those months were settled, and
inventing a method would put unverifiable data into an audit trail.

payment_date is left NULL for the same reason. Display code falls back to the
paid_at date, which is a real recorded timestamp, so nothing appears empty in
the UI while the distinction stays honest in the data.

Reverse migration simply drops the three columns; no data loss beyond the
Phase E6 fields themselves.
"""

from django.db import migrations, models


def backfill_amount_paid(apps, schema_editor):
    """Set amount_paid = final_salary on every existing row.

    Uses an F() expression so this is a single UPDATE regardless of row count,
    and touches only the new column.
    """
    PaidSalaryRecord = apps.get_model('payroll', 'PaidSalaryRecord')
    PaidSalaryRecord.objects.filter(amount_paid__isnull=True).update(
        amount_paid=models.F('final_salary')
    )


def unbackfill_amount_paid(apps, schema_editor):
    """No-op on reverse — the column itself is dropped by the schema operation."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('payroll', '0025_payroll_note'),
    ]

    operations = [
        migrations.AddField(
            model_name='paidsalaryrecord',
            name='amount_paid',
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=12, null=True,
                help_text=(
                    'Amount actually disbursed. Equals final_salary for a full '
                    'payment, less for a partial one. NULL only on rows created '
                    'before partial payments existed — those are treated as paid '
                    'in full.'
                ),
            ),
        ),
        migrations.AddField(
            model_name='paidsalaryrecord',
            name='payment_method',
            field=models.CharField(
                blank=True, max_length=20,
                choices=[('wps', 'WPS'), ('bank_transfer', 'Bank Transfer'), ('cash', 'Cash')],
                help_text=(
                    "How the disbursement was made. 'bank_transfer' is labelled "
                    "with the employee's visa provider at display time. Blank on "
                    'legacy rows.'
                ),
            ),
        ),
        migrations.AddField(
            model_name='paidsalaryrecord',
            name='payment_date',
            field=models.DateField(
                blank=True, null=True,
                help_text=(
                    'Value date of the disbursement. Distinct from paid_at, which '
                    'is the timestamp the record was created in the system.'
                ),
            ),
        ),
        migrations.RunPython(backfill_amount_paid, unbackfill_amount_paid),
    ]
