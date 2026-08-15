import django.db.models.deletion
from django.db import migrations, models

VISA_TYPE_CHOICES = [
    ('employment', 'Employment Visa'),
    ('investor', 'Investor / Partner Visa'),
    ('golden', 'Golden Visa'),
    ('dependent', 'Dependent Visa'),
    ('freelance', 'Freelance Permit'),
    ('other', 'Other'),
]

CONTRACT_TYPE_CHOICES = [
    ('limited', 'Limited'),
    ('unlimited', 'Unlimited (legacy)'),
]

FIELDS = [
    ('visa_type', models.CharField(
        blank=True, choices=VISA_TYPE_CHOICES, default='', max_length=20,
        help_text="Category of UAE visa. The number and expiry live on the "
                  "employee's UAE Visa document record, not here.")),
    ('contract_type', models.CharField(
        blank=True, choices=CONTRACT_TYPE_CHOICES, default='', max_length=20,
        help_text='Gratuity computation basis. Deliberately blank by default: '
                  'an unrecorded contract type must read as unknown, never as '
                  'a guess, because the guess would change an end-of-service '
                  'figure without anyone deciding to.')),
    ('probation_end_date', models.DateField(
        blank=True, null=True,
        help_text='Leave blank to use joining date + 90 days. Set it only when '
                  'the real end date differs — a stored value always wins, and '
                  'is shown as confirmed rather than inferred.')),
    ('taamul_connect_user_id', models.CharField(
        blank=True, db_index=True, default='', max_length=100,
        help_text='TaamulConnect user ID — API sync key and the handle used to '
                  'provision or revoke access.')),
    ('commission_plan_code', models.CharField(
        blank=True, db_index=True, default='', max_length=30,
        help_text='Bridge to the DSA commission engine. Not applicable to '
                  'Admin-department staff.')),
    ('compliance_reviewed_at', models.DateTimeField(
        blank=True, null=True,
        help_text="When this employee's compliance record was last confirmed "
                  'correct by a human.')),
    ('compliance_reviewed_by', models.CharField(
        blank=True, default='', max_length=150, help_text='Who confirmed it.')),
]


class Migration(migrations.Migration):
    """Additive only. Every field is blank/null-able with a safe default, so
    no existing row is rewritten and nothing is inferred on a person nobody
    has looked at yet."""

    dependencies = [
        ('attendance', '0051_userprofile_role'),
    ]

    operations = [
        migrations.AddField(model_name=model, name=name, field=field)
        for model in ('employee', 'remoteemployee')
        for name, field in FIELDS
    ]
