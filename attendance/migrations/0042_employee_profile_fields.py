# Generated manually — Phase 3: Employee 360° Profile Fields
# Adds personal, identity, bank, lifecycle and onboarding fields to Employee.
# Purely additive — no existing columns or tables are modified.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0041_add_master_data_tables'),
    ]

    operations = [
        # ── Org Hierarchy ───────────────────────────────────────────────────────
        migrations.AddField(
            model_name='employee',
            name='reporting_manager',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='direct_reports',
                to='attendance.employee',
                help_text='Direct reporting manager for this employee',
            ),
        ),

        # ── Personal Information ────────────────────────────────────────────────
        migrations.AddField(
            model_name='employee',
            name='date_of_birth',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='employee',
            name='gender',
            field=models.CharField(
                blank=True,
                choices=[('M', 'Male'), ('F', 'Female'), ('O', 'Other')],
                max_length=1,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='employee',
            name='blood_group',
            field=models.CharField(
                blank=True,
                choices=[
                    ('A+', 'A+'), ('A-', 'A-'),
                    ('B+', 'B+'), ('B-', 'B-'),
                    ('O+', 'O+'), ('O-', 'O-'),
                    ('AB+', 'AB+'), ('AB-', 'AB-'),
                ],
                max_length=4,
                null=True,
            ),
        ),

        # ── Emergency Contact ───────────────────────────────────────────────────
        migrations.AddField(
            model_name='employee',
            name='emergency_contact_name',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name='employee',
            name='emergency_contact_phone',
            field=models.CharField(blank=True, max_length=20, null=True),
        ),

        # ── Identity Documents ──────────────────────────────────────────────────
        migrations.AddField(
            model_name='employee',
            name='national_id',
            field=models.CharField(
                blank=True,
                max_length=50,
                null=True,
                help_text='Emirates ID / Aadhaar / National ID number',
            ),
        ),
        migrations.AddField(
            model_name='employee',
            name='passport_number',
            field=models.CharField(blank=True, max_length=30, null=True),
        ),

        # ── Bank Details ────────────────────────────────────────────────────────
        migrations.AddField(
            model_name='employee',
            name='bank_name',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name='employee',
            name='bank_account_number',
            field=models.CharField(blank=True, max_length=50, null=True),
        ),
        migrations.AddField(
            model_name='employee',
            name='bank_routing_code',
            field=models.CharField(
                blank=True,
                max_length=30,
                null=True,
                help_text='IFSC (India) / IBAN / Routing code depending on bank country',
            ),
        ),

        # ── Profile Photo ───────────────────────────────────────────────────────
        migrations.AddField(
            model_name='employee',
            name='profile_photo',
            field=models.ImageField(
                blank=True,
                null=True,
                upload_to='employee_photos/',
                help_text='Employee passport-style photo',
            ),
        ),

        # ── Employment Lifecycle ────────────────────────────────────────────────
        migrations.AddField(
            model_name='employee',
            name='employment_status',
            field=models.CharField(
                choices=[
                    ('active', 'Active'),
                    ('on_notice', 'On Notice'),
                    ('relieved', 'Relieved'),
                    ('absconded', 'Absconded'),
                ],
                db_index=True,
                default='active',
                max_length=20,
                help_text='Current employment lifecycle stage',
            ),
        ),
        migrations.AddField(
            model_name='employee',
            name='notice_date',
            field=models.DateField(
                blank=True,
                null=True,
                help_text='Date notice period began',
            ),
        ),
        migrations.AddField(
            model_name='employee',
            name='relieving_date',
            field=models.DateField(
                blank=True,
                null=True,
                help_text='Official last working day / relieving date',
            ),
        ),

        # ── Onboarding Checklist ────────────────────────────────────────────────
        migrations.AddField(
            model_name='employee',
            name='onboarding_checklist',
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text='Dict of checklist item keys → True/False completion state',
            ),
        ),
    ]
