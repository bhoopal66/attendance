"""
Migration 0024 — Targets & Revenue (Phase 10)

Additive only:
  1. AddField  Bank.revenue_per_account   (nullable — no default required,
     existing Bank rows are untouched)
  2. CreateModel EmployeeTarget           (new table)
  3. AddIndex / AddConstraint for EmployeeTarget

No existing table is altered destructively, no data migration needed
(forward-only feature — targets are entered going forward).

Depends on payroll 0023 (PayrollRun).
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0047_recoverable'),
        ('payroll', '0023_payroll_run'),
    ]

    operations = [
        # ── 1. Bank.revenue_per_account ──────────────────────────────────────
        migrations.AddField(
            model_name='bank',
            name='revenue_per_account',
            field=models.DecimalField(
                max_digits=10, decimal_places=2,
                null=True, blank=True,
                help_text='Revenue earned by the company per account submitted to this bank (AED). '
                          'Used for performance & profitability reporting. Leave blank if not tracked.',
            ),
        ),

        # ── 2. EmployeeTarget ────────────────────────────────────────────────
        migrations.CreateModel(
            name='EmployeeTarget',
            fields=[
                ('id', models.BigAutoField(
                    auto_created=True, primary_key=True,
                    serialize=False, verbose_name='ID',
                )),
                ('year',  models.IntegerField()),
                ('month', models.IntegerField(help_text='1–12')),
                ('target_accounts', models.PositiveIntegerField(
                    default=0,
                    help_text='Number of funded accounts targeted for the month.',
                )),
                ('notes', models.CharField(max_length=255, blank=True)),
                ('created_by', models.CharField(max_length=150, blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_by', models.CharField(max_length=150, blank=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('employee', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='monthly_targets',
                    to='attendance.employee',
                )),
                ('remote_employee', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='monthly_targets',
                    to='attendance.remoteemployee',
                )),
            ],
            options={
                'verbose_name': 'Employee Target',
                'verbose_name_plural': 'Employee Targets',
                'ordering': ['-year', '-month'],
            },
        ),

        # ── 3. Indexes ───────────────────────────────────────────────────────
        migrations.AddIndex(
            model_name='employeetarget',
            index=models.Index(
                fields=['employee', 'year', 'month'],
                name='pay__tgt_emp_ym_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='employeetarget',
            index=models.Index(
                fields=['remote_employee', 'year', 'month'],
                name='pay__tgt_remp_ym_idx',
            ),
        ),

        # ── 4. Uniqueness constraints (one target per person per month) ──────
        migrations.AddConstraint(
            model_name='employeetarget',
            constraint=models.UniqueConstraint(
                fields=['employee', 'year', 'month'],
                condition=models.Q(employee__isnull=False),
                name='unique_inhouse_target_month',
            ),
        ),
        migrations.AddConstraint(
            model_name='employeetarget',
            constraint=models.UniqueConstraint(
                fields=['remote_employee', 'year', 'month'],
                condition=models.Q(remote_employee__isnull=False),
                name='unique_remote_target_month',
            ),
        ),
    ]
