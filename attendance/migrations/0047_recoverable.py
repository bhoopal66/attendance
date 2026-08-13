"""
Migration 0047 — Recoverable Sub-Ledger (Phase 8)

Additive only: creates the Recoverable table and its two composite indexes.
No existing data is altered or removed.

Depends on 0046 (EmployeeDocument).
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0046_employee_document'),
    ]

    operations = [
        migrations.CreateModel(
            name='Recoverable',
            fields=[
                ('id', models.BigAutoField(
                    auto_created=True, primary_key=True,
                    serialize=False, verbose_name='ID',
                )),
                ('recoverable_type', models.CharField(
                    max_length=30,
                    choices=[
                        ('visa_cost',  'Visa Cost'),
                        ('advance',    'Salary Advance'),
                        ('asset',      'Asset / Equipment'),
                        ('training',   'Training Cost'),
                        ('air_ticket', 'Air Ticket'),
                        ('relocation', 'Relocation Cost'),
                        ('other',      'Other'),
                    ],
                    help_text='Nature of the amount to be recovered',
                )),
                ('description', models.CharField(
                    max_length=255,
                    help_text='Brief description of what this recoverable is for',
                )),
                ('total_amount', models.DecimalField(
                    max_digits=12, decimal_places=2,
                    help_text='Total amount to be recovered from the employee',
                )),
                ('currency', models.CharField(
                    max_length=3, default='AED',
                    help_text='ISO currency code (e.g. AED, INR, NPR)',
                )),
                ('monthly_recovery', models.DecimalField(
                    max_digits=10, decimal_places=2, default=0,
                    help_text='Planned monthly deduction amount',
                )),
                ('recovery_start_year', models.IntegerField(
                    help_text='Year in which monthly recovery begins',
                )),
                ('recovery_start_month', models.IntegerField(
                    help_text='Month (1-12) in which monthly recovery begins',
                )),
                ('amount_recovered', models.DecimalField(
                    max_digits=12, decimal_places=2, default=0,
                    help_text='Cumulative amount recovered to date (updated by payroll or admin)',
                )),
                ('status', models.CharField(
                    max_length=20,
                    choices=[
                        ('active',  'Active'),
                        ('settled', 'Settled'),
                        ('waived',  'Waived'),
                        ('on_hold', 'On Hold'),
                    ],
                    default='active',
                    db_index=True,
                    help_text="'active' = recovery ongoing; 'settled' = fully paid; "
                              "'waived' = written off; 'on_hold' = paused",
                )),
                ('notes', models.TextField(
                    blank=True,
                    help_text='Additional context or comments',
                )),
                ('waived_by', models.CharField(
                    max_length=150, blank=True,
                    help_text='Username who authorised the waiver',
                )),
                ('waived_at', models.DateTimeField(
                    null=True, blank=True,
                    help_text='When the waiver was granted',
                )),
                ('waived_reason', models.TextField(
                    blank=True,
                    help_text='Reason the outstanding amount was waived',
                )),
                ('created_by', models.CharField(
                    max_length=150,
                    help_text='Username who created this record',
                )),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('employee', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='recoverables',
                    to='attendance.employee',
                    help_text='In-house employee (leave blank for remote)',
                )),
                ('remote_employee', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='recoverables',
                    to='attendance.remoteemployee',
                    help_text='Remote employee (leave blank for in-house)',
                )),
            ],
            options={
                'verbose_name': 'Recoverable',
                'verbose_name_plural': 'Recoverables',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='recoverable',
            index=models.Index(
                fields=['employee', 'status'],
                name='att__rec_emp_status_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='recoverable',
            index=models.Index(
                fields=['remote_employee', 'status'],
                name='att__rec_remp_status_idx',
            ),
        ),
    ]
