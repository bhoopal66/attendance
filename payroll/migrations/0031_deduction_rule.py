"""Phase 4 - deduction rules & limits.

Adds the `DeductionRule` table. **Seeds nothing.**

That is the point of this migration as much as the table is. Statutory
deduction limits are a legal question; shipping an invented percentage
switched on would put an authoritative-looking, unsourced ceiling in front of
real salaries. The table arrives empty, `is_active` defaults to False, and
`DeductionRule.clean()` refuses to activate a rule with no `legal_reference`.

Touches no existing table, so it is safe against the live payroll database and
reverses without data loss.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payroll', '0030_loans'),
    ]

    operations = [
        migrations.CreateModel(
            name='DeductionRule',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.SlugField(max_length=40, unique=True)),
                ('name', models.CharField(max_length=150)),
                ('description', models.TextField(blank=True)),
                ('scope', models.CharField(choices=[('all', 'All recorded deductions combined'), ('type', 'One deduction type'), ('loans', 'Loan instalments only')], default='all', max_length=10)),
                ('deduction_code', models.CharField(blank=True, help_text="DeductionType code this rule limits. Only used when scope is 'One deduction type'.", max_length=30)),
                ('basis', models.CharField(choices=[('basic', 'Basic salary'), ('gross', 'Gross salary')], default='basic', max_length=10)),
                ('max_percent', models.DecimalField(blank=True, decimal_places=3, help_text='Ceiling as a percentage of the basis, e.g. 25.000.', max_digits=6, null=True)),
                ('max_amount', models.DecimalField(blank=True, decimal_places=2, help_text='Absolute ceiling. Applied alongside the percentage; whichever is lower binds.', max_digits=12, null=True)),
                ('amount_currency', models.CharField(default='AED', help_text='Currency of max_amount. The rule is skipped for employees paid in another currency rather than converted at an assumed rate.', max_length=3)),
                ('applies_to', models.CharField(choices=[('all', 'All employees'), ('inhouse', 'In-house only'), ('remote', 'Remote only')], default='all', max_length=10)),
                ('department', models.CharField(blank=True, help_text='Limit to one department. Blank means every department.', max_length=100)),
                ('enforcement', models.CharField(choices=[('warn', 'Warn - allow, but flag it'), ('block', 'Block - refuse the entry')], default='warn', help_text="'Warn' records the breach and lets the entry through. 'Block' refuses it.", max_length=10)),
                ('is_active', models.BooleanField(default=False, help_text='Off by default. A rule enforces nothing until this is on.')),
                ('legal_reference', models.CharField(blank=True, help_text='The law, ministerial resolution or company policy this ceiling comes from. Required before the rule can be activated.', max_length=255)),
                ('effective_from_year', models.IntegerField()),
                ('effective_from_month', models.IntegerField(help_text='1-12')),
                ('effective_to_year', models.IntegerField(blank=True, null=True)),
                ('effective_to_month', models.IntegerField(blank=True, help_text='1-12', null=True)),
                ('created_by', models.CharField(blank=True, max_length=150)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_by', models.CharField(blank=True, max_length=150)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Deduction Rule',
                'verbose_name_plural': 'Deduction Rules',
                'ordering': ['-is_active', 'name'],
            },
        ),
    ]
