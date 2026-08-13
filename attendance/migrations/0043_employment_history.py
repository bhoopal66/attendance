"""
Migration 0043 — Employment History table (Phase 4)

Creates the EmploymentHistory model: an immutable, effective-dated log of
key employment changes (designation, department, team, location,
reporting_manager, employment_status, salary) for in-house employees.

Type: purely additive — CreateModel only, zero destructive operations.
Dependency: 0042_employee_profile_fields
"""

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0042_employee_profile_fields'),
    ]

    operations = [
        migrations.CreateModel(
            name='EmploymentHistory',
            fields=[
                ('id', models.BigAutoField(
                    auto_created=True,
                    primary_key=True,
                    serialize=False,
                    verbose_name='ID',
                )),
                ('employee', models.ForeignKey(
                    help_text='The in-house employee this change belongs to',
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='employment_history',
                    to='attendance.employee',
                )),
                ('change_type', models.CharField(
                    choices=[
                        ('designation',       'Designation'),
                        ('department',        'Department'),
                        ('team',              'Team'),
                        ('location',          'Location'),
                        ('reporting_manager', 'Reporting Manager'),
                        ('employment_status', 'Employment Status'),
                        ('salary',            'Salary'),
                        ('other',             'Other'),
                    ],
                    db_index=True,
                    help_text='Which aspect of employment changed',
                    max_length=30,
                )),
                ('effective_date', models.DateField(
                    help_text='Calendar date on which this change took effect',
                )),
                ('previous_value', models.JSONField(
                    blank=True,
                    null=True,
                    help_text='Snapshot of the value before the change (null for first-ever entry)',
                )),
                ('new_value', models.JSONField(
                    help_text='Snapshot of the value after the change',
                )),
                ('reason', models.TextField(
                    blank=True,
                    help_text='Optional business reason or comment for this change',
                )),
                ('changed_by', models.CharField(
                    help_text='Username of the admin who made the change',
                    max_length=150,
                )),
                ('changed_at', models.DateTimeField(
                    auto_now_add=True,
                    help_text='Server timestamp when the change was recorded (immutable)',
                )),
            ],
            options={
                'verbose_name': 'Employment History',
                'verbose_name_plural': 'Employment History',
                'ordering': ['-effective_date', '-changed_at'],
            },
        ),
        migrations.AddIndex(
            model_name='employmenthistory',
            index=models.Index(
                fields=['employee', '-effective_date'],
                name='attendance__employm_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='employmenthistory',
            index=models.Index(
                fields=['change_type'],
                name='attendance__change_type_idx',
            ),
        ),
    ]
