"""
Migration 0022 — DeductionEntry: add recoverable FK (Phase 8)

Additive only: adds a nullable ForeignKey to attendance.Recoverable on the
DeductionEntry model. No existing rows are affected (all existing entries
will have recoverable=NULL).

Depends on:
  - payroll 0021 (latest payroll migration)
  - attendance 0047 (Recoverable model must exist before referencing it)
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payroll', '0021_deductioncarryover_currency_deductionentry_currency'),
        ('attendance', '0047_recoverable'),
    ]

    operations = [
        migrations.AddField(
            model_name='deductionentry',
            name='recoverable',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='deduction_entries',
                to='attendance.recoverable',
                help_text='Link this deduction to a Recoverable sub-ledger row (optional). '
                          'Phase 9 will use this to auto-update amount_recovered.',
            ),
        ),
    ]
