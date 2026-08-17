"""Annual leave defaults to 50% of gross, not 100%.

AlterField ONLY. This deliberately does not touch a single existing row.

A data migration setting every AnnualLeave to 50 would rewrite the value of
leave spans inside months that have already been calculated, locked and paid —
a payslip issued at 100% would silently become a 50% payslip, and the payroll
snapshot would no longer reconcile against the run it came from. The rule
therefore applies to new entries; anything already recorded keeps the
percentage it was saved with, and is changed one row at a time, on purpose, if
it needs to be.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0053_auditlog_view_action'),
    ]

    operations = [
        migrations.AlterField(
            model_name='annualleave',
            name='salary_percentage',
            field=models.DecimalField(
                decimal_places=2, default=50, max_digits=5,
                help_text='Percentage of normal salary paid during leave (0–100). '
                          'Only relevant when is_paid=True. Company rule: annual '
                          'leave is paid at 50% of gross, on the same daily rate '
                          'as paid leave and paid holidays (gross / days in the '
                          'pay period). Existing rows keep whatever percentage '
                          'they were saved with — this default changes new '
                          'entries only, so no month already paid moves.',
            ),
        ),
    ]
