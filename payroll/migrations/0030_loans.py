"""Phase 3 - Loans & Salary Advances.

Adds `Loan` and `LoanInstallment`, and seeds the `loan_repayment` deduction
type that instalments are posted under.

TOUCHES NO EXISTING TABLE.
Both models are new; the only write to pre-existing data is one new row in the
Phase 2 `DeductionType` registry. `DeductionEntry`, `PaidSalaryRecord` and
every other money-bearing table are left exactly as they are, which is what
makes this safe to run against the live payroll database and reversible
without data loss.

`loan_repayment` is seeded with allow_manual_entry=False on purpose: a loan
repayment must originate from a loan, so the Add Deduction form will refuse it.
Typing one by hand would create a repayment that no schedule knows about, and
the loan would never reconcile.
"""

import django.db.models.deletion
from django.db import migrations, models


LOAN_TYPE = {
    'code': 'loan_repayment',
    'name': 'Loan Repayment',
    'entry_type': 'deduction',
    'classification': 'recovery',
    'description': ('One monthly instalment of a loan or salary advance. '
                    'Created automatically when a loan is activated - manage it '
                    'from the Loans page, not here.'),
    'is_active': True,
    'is_system': True,
    'allow_manual_entry': False,
    'allow_split_months': False,
    'rolls_up_to_other': True,
    'requires_note': False,
    'gl_account_code': '',
    'colour': '#7c3aed',
    'sort_order': 15,
    'created_by': 'migration 0030',
}


def seed_loan_type(apps, schema_editor):
    DeductionType = apps.get_model('payroll', 'DeductionType')
    db = schema_editor.connection.alias
    code = LOAN_TYPE['code']
    DeductionType.objects.using(db).get_or_create(
        code=code, defaults={k: v for k, v in LOAN_TYPE.items() if k != 'code'})


def unseed_loan_type(apps, schema_editor):
    """Remove the seeded type only if nothing was ever posted against it.

    If instalments exist, the entries carrying this code must keep a label -
    reversing the migration should not orphan them.
    """
    DeductionType = apps.get_model('payroll', 'DeductionType')
    DeductionEntry = apps.get_model('payroll', 'DeductionEntry')
    db = schema_editor.connection.alias
    if DeductionEntry.objects.using(db).filter(category=LOAN_TYPE['code']).exists():
        return
    DeductionType.objects.using(db).filter(code=LOAN_TYPE['code'], is_system=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0049_rename_attendance__employm_idx_attendance__employe_d26f67_idx_and_more'),
        ('payroll', '0029_deduction_type'),
    ]

    operations = [
        migrations.CreateModel(
            name='Loan',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reference', models.CharField(help_text='Human reference, e.g. LN-2026-0007. Generated on save when blank.', max_length=24, unique=True)),
                ('purpose', models.CharField(choices=[('advance', 'Salary Advance'), ('visa_cost', 'Visa Cost'), ('asset', 'Asset / Equipment'), ('training', 'Training Cost'), ('air_ticket', 'Air Ticket'), ('relocation', 'Relocation Cost'), ('other', 'Other')], default='advance', max_length=30)),
                ('description', models.CharField(max_length=255)),
                ('principal', models.DecimalField(decimal_places=2, help_text='Total amount advanced. Instalments always sum to exactly this.', max_digits=12)),
                ('currency', models.CharField(default='AED', help_text="Set from the employee's currency when the loan is created.", max_length=3)),
                ('installment_count', models.PositiveIntegerField(default=1, help_text='Number of monthly instalments (1 = recovered in full next month).')),
                ('first_deduction_year', models.IntegerField()),
                ('first_deduction_month', models.IntegerField(help_text='1-12')),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('active', 'Active'), ('settled', 'Settled'), ('cancelled', 'Cancelled'), ('on_hold', 'On Hold')], db_index=True, default='draft', help_text='Draft loans deduct nothing. Activating writes the deduction entries.', max_length=20)),
                ('note', models.TextField(blank=True)),
                ('created_by', models.CharField(blank=True, max_length=150)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('activated_by', models.CharField(blank=True, max_length=150)),
                ('activated_at', models.DateTimeField(blank=True, null=True)),
                ('closed_by', models.CharField(blank=True, max_length=150)),
                ('closed_at', models.DateTimeField(blank=True, null=True)),
                ('closed_reason', models.CharField(blank=True, max_length=255)),
                ('employee', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='loans', to='attendance.employee')),
                ('remote_employee', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='loans', to='attendance.remoteemployee')),
                ('recoverable', models.OneToOneField(blank=True, help_text='The sub-ledger row this loan keeps in step. Created with the loan.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='loan', to='attendance.recoverable')),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='LoanInstallment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('sequence', models.PositiveIntegerField(help_text='1-based position in the schedule.')),
                ('year', models.IntegerField()),
                ('month', models.IntegerField(help_text='1-12')),
                ('due_amount', models.DecimalField(decimal_places=2, max_digits=12)),
                ('amount_recovered', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('status', models.CharField(choices=[('scheduled', 'Scheduled'), ('posted', 'Posted to payroll'), ('recovered', 'Recovered'), ('waived', 'Waived'), ('skipped', 'Skipped')], db_index=True, default='scheduled', max_length=20)),
                ('note', models.CharField(blank=True, max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('deduction_entry', models.ForeignKey(blank=True, help_text='The payroll deduction this instalment created, if posted.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='loan_installments', to='payroll.deductionentry')),
                ('loan', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='installments', to='payroll.loan')),
            ],
            options={'ordering': ['loan_id', 'sequence']},
        ),
        migrations.AddIndex(
            model_name='loan',
            index=models.Index(fields=['employee', 'status'], name='payroll_loa_employe_2b1f3c_idx'),
        ),
        migrations.AddIndex(
            model_name='loan',
            index=models.Index(fields=['remote_employee', 'status'], name='payroll_loa_remote__7d4a91_idx'),
        ),
        migrations.AddIndex(
            model_name='loaninstallment',
            index=models.Index(fields=['year', 'month', 'status'], name='payroll_loa_year_c5e802_idx'),
        ),
        migrations.AddConstraint(
            model_name='loaninstallment',
            constraint=models.UniqueConstraint(fields=('loan', 'sequence'), name='uniq_loan_sequence'),
        ),
        migrations.AddConstraint(
            model_name='loaninstallment',
            constraint=models.UniqueConstraint(fields=('loan', 'year', 'month'), name='uniq_loan_period'),
        ),
        migrations.RunPython(seed_loan_type, unseed_loan_type),
    ]
