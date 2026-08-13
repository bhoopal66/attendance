"""
Migration 0025 — Payroll Notes (Phase C)

Additive only: CreateModel PayrollNote (new table) + two indexes.
No existing table is altered. Depends on the latest attendance migration
(0048_audit_log) since PayrollNote FKs to attendance.Employee /
attendance.RemoteEmployee, and on payroll 0024 (last applied payroll migration).
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0048_audit_log'),
        ('payroll', '0024_targets_revenue'),
    ]

    operations = [
        migrations.CreateModel(
            name='PayrollNote',
            fields=[
                ('id', models.BigAutoField(
                    auto_created=True, primary_key=True,
                    serialize=False, verbose_name='ID',
                )),
                ('text', models.TextField()),
                ('created_by', models.CharField(max_length=150, blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('employee', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='payroll_notes',
                    to='attendance.employee',
                )),
                ('remote_employee', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='payroll_notes',
                    to='attendance.remoteemployee',
                )),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='payrollnote',
            index=models.Index(
                fields=['employee', '-created_at'],
                name='payroll_pay_employe_a1f2c3_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='payrollnote',
            index=models.Index(
                fields=['remote_employee', '-created_at'],
                name='payroll_pay_remote__b4d5e6_idx',
            ),
        ),
    ]
