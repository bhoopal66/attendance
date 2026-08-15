"""Sunday Entitlement — persisted calculation, breakdown and HR override.

Stores the individual Sunday dates and the reason each was included or
excluded, not just a count. A bare "3" cannot be defended six months later,
and recomputing it then can give a different answer because the underlying
joining or leave records have since been edited.

`system_calculated_count` is written once and never overwritten. An override
lands in `override_count` beside it with a reason and an actor, so payroll
always holds both numbers.

Creates one table. Alters nothing.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0050_annualleave_actual_rejoining_date'),
        ('payroll', '0032_paid_holidays'),
    ]

    operations = [
        migrations.CreateModel(
            name='SundayEntitlementRecord',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('year', models.IntegerField()),
                ('month', models.IntegerField(help_text='1-12')),
                ('period_start', models.DateField()),
                ('period_end', models.DateField()),
                ('total_sundays', models.PositiveIntegerField(default=0)),
                ('system_calculated_count', models.PositiveIntegerField(default=0, help_text='What the engine calculated. Never overwritten by an override.')),
                ('override_count', models.PositiveIntegerField(blank=True, help_text='HR-approved figure, when it differs from the calculation.', null=True)),
                ('basis', models.CharField(blank=True, max_length=60)),
                ('eligibility_start_date', models.DateField(blank=True, null=True)),
                ('breakdown', models.JSONField(blank=True, default=list, help_text='Every Sunday in the period with its verdict and reason, so the figure can be reconstructed exactly as it was calculated.')),
                ('calculated_at', models.DateTimeField(auto_now=True)),
                ('override_reason', models.TextField(blank=True)),
                ('override_by', models.CharField(blank=True, max_length=150)),
                ('override_at', models.DateTimeField(blank=True, null=True)),
                ('employee', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='sunday_entitlements', to='attendance.employee')),
                ('remote_employee', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='sunday_entitlements', to='attendance.remoteemployee')),
            ],
            options={
                'verbose_name': 'Sunday Entitlement',
                'verbose_name_plural': 'Sunday Entitlements',
                'ordering': ['-year', '-month'],
            },
        ),
        migrations.AddIndex(
            model_name='sundayentitlementrecord',
            index=models.Index(fields=['year', 'month'], name='payroll_sun_year_8c3d21_idx'),
        ),
        migrations.AddConstraint(
            model_name='sundayentitlementrecord',
            constraint=models.UniqueConstraint(condition=models.Q(('employee__isnull', False)), fields=('employee', 'year', 'month'), name='uniq_sunday_inhouse_period'),
        ),
        migrations.AddConstraint(
            model_name='sundayentitlementrecord',
            constraint=models.UniqueConstraint(condition=models.Q(('remote_employee__isnull', False)), fields=('remote_employee', 'year', 'month'), name='uniq_sunday_remote_period'),
        ),
    ]
