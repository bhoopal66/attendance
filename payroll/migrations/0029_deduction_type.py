"""Phase 2 - Deduction Master.

Adds the configurable DeductionType registry and seeds it with the nine
categories that were previously hard-coded in DEDUCTION_CATEGORY_CHOICES.

DELIBERATELY DOES NOT TOUCH DeductionEntry.
No column is added, altered or dropped on the table that holds real money.
`DeductionEntry.category` keeps the same values it has today; this migration
only creates a lookup table describing them. That is what makes it reversible
without data loss and safe to run against the live payroll database.

The seeded flags reproduce current behaviour exactly:
  * rolls_up_to_other mirrors payroll.models.OTHER_DEDUCTION_CATEGORIES
  * allow_manual_entry is ON for all nine, including the two attendance-derived
    types, because the Add Deduction form offers them today. Turning Leave and
    Late off is recommended - payroll already computes them from attendance, so
    a manual entry deducts the same absence twice - but that is a change of
    behaviour and therefore the operator's decision, not this migration's.
  * requires_note is off everywhere, so nothing that saves today starts failing
"""

from django.db import migrations, models


# (code, name, entry_type, classification, manual, split, rollup, colour, sort)
SEED = [
    ('advance',            'Advance',            'deduction', 'recovery',    True,  True,  False, '#c2410c', 10),
    ('visa_status_change', 'Visa Status Change', 'deduction', 'recovery',    True,  True,  True,  '#9a3412', 20),
    ('clawback',           'Clawback',           'deduction', 'contractual', True,  True,  True,  '#b91c1c', 30),
    ('leave_deduction',    'Leave Deduction',    'deduction', 'attendance',  True,  True,  False, '#a16207', 40),
    ('late_deduction',     'Late Deduction',     'deduction', 'attendance',  True,  True,  False, '#a16207', 50),
    ('other_deduction',    'Other Deduction',    'deduction', 'other',       True,  True,  True,  '#64748b', 60),
    ('last_month_balance', 'Last Month Balance', 'addition',  'other',       True,  True,  False, '#166534', 70),
    ('paid_leave',         'Paid Leave',         'addition',  'contractual', True,  True,  False, '#15803d', 80),
    ('other_addition',     'Others',             'addition',  'other',       True,  True,  False, '#166534', 90),
]

DESCRIPTIONS = {
    'advance':            'Salary paid to the employee ahead of payday, recovered from a later month.',
    'visa_status_change': 'Cost recovered from the employee when their visa status changes.',
    'clawback':           'Recovery of commission or incentive paid on business that was later reversed.',
    'leave_deduction':    'Unpaid leave. Payroll already calculates this from attendance; entering it by hand as well deducts the same absence twice.',
    'late_deduction':     'Lateness. Payroll already calculates this from attendance; entering it by hand as well deducts the same lateness twice.',
    'other_deduction':    'Any deduction that does not fit the categories above.',
    'last_month_balance': 'Amount carried forward and paid in this month.',
    'paid_leave':         'Leave paid to the employee.',
    'other_addition':     'Any addition that does not fit the categories above.',
}


def seed_types(apps, schema_editor):
    DeductionType = apps.get_model('payroll', 'DeductionType')
    db = schema_editor.connection.alias
    for (code, name, etype, classification, manual, split,
         rollup, colour, order) in SEED:
        # get_or_create, not create: this migration must be safe to re-run
        # against a database where an operator has already added the rows.
        DeductionType.objects.using(db).get_or_create(
            code=code,
            defaults={
                'name': name,
                'entry_type': etype,
                'classification': classification,
                'description': DESCRIPTIONS.get(code, ''),
                'is_active': True,
                'is_system': True,
                'allow_manual_entry': manual,
                'allow_split_months': split,
                'rolls_up_to_other': rollup,
                'requires_note': False,
                'gl_account_code': '',
                'colour': colour,
                'sort_order': order,
                'created_by': 'migration 0029',
            },
        )


def unseed_types(apps, schema_editor):
    """Remove only the rows this migration created.

    Any type an operator added by hand is left alone - reversing a migration
    should undo the migration, not delete the user's configuration. The table
    itself is dropped by the reverse of CreateModel immediately afterwards, so
    this mainly documents intent and keeps a partial rollback honest.
    """
    DeductionType = apps.get_model('payroll', 'DeductionType')
    db = schema_editor.connection.alias
    DeductionType.objects.using(db).filter(
        code__in=[row[0] for row in SEED], is_system=True
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('payroll', '0028_paidsalaryrecord_payment_splits_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='DeductionType',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.SlugField(help_text='Stable identifier stored on every deduction entry. Cannot exceed 30 characters - it must fit DeductionEntry.category - and cannot be changed once used.', max_length=30, unique=True)),
                ('name', models.CharField(help_text='Label shown to users.', max_length=120)),
                ('entry_type', models.CharField(choices=[('deduction', 'Deduction'), ('addition', 'Addition')], default='deduction', help_text='Whether this reduces or increases net pay. Fixed at creation - flipping it would reverse the sign of every historical entry already carrying this code.', max_length=10)),
                ('classification', models.CharField(choices=[('statutory', 'Statutory'), ('contractual', 'Contractual'), ('recovery', 'Recovery / Loan'), ('attendance', 'Attendance-derived'), ('disciplinary', 'Disciplinary'), ('voluntary', 'Voluntary'), ('other', 'Other')], default='other', help_text='Reporting grouping only. Does not affect calculation.', max_length=20)),
                ('description', models.TextField(blank=True)),
                ('is_active', models.BooleanField(default=True, help_text='Inactive types disappear from the entry form. Existing entries continue to be deducted - deactivating a type is not a way to cancel money already scheduled.')),
                ('is_system', models.BooleanField(default=False, help_text='One of the nine original built-in categories. Payroll calculation and dashboard columns refer to these codes directly, so they cannot be renamed at code level or deleted.')),
                ('allow_manual_entry', models.BooleanField(default=True, help_text='Off for types payroll derives itself from attendance (leave, late). Adding those by hand deducts the same absence twice.')),
                ('allow_split_months', models.BooleanField(default=True, help_text='Whether the amount may be spread over several months.')),
                ('rolls_up_to_other', models.BooleanField(default=True, help_text='Show this amount in the dashboard "Other" deductions column. Only the four types with a dedicated column (Late, Leave, Advance, Carryover) may turn this off - every other deduction must land in exactly one column or the itemized figures stop summing to the total.')),
                ('requires_note', models.BooleanField(default=False, help_text='Refuse to save an entry of this type without a note.')),
                ('gl_account_code', models.CharField(blank=True, help_text='General ledger account. Recorded for the future GL export; nothing posts to a ledger yet.', max_length=40)),
                ('colour', models.CharField(blank=True, default='', help_text='Hex colour for the badge, e.g. #eb6834. Blank uses the default grey.', max_length=7)),
                ('sort_order', models.PositiveIntegerField(default=100, help_text='Lower numbers appear first in menus.')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.CharField(blank=True, max_length=150)),
                ('updated_by', models.CharField(blank=True, max_length=150)),
            ],
            options={
                'verbose_name': 'Deduction Type',
                'verbose_name_plural': 'Deduction Types',
                'ordering': ['sort_order', 'name'],
            },
        ),
        migrations.RunPython(seed_types, unseed_types),
    ]
