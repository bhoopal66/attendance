"""
Migration 0046 — Employee Document Management (Phase 7)

Additive only: creates the EmployeeDocument table and its two indexes.
No existing data is altered or removed.

Depends on 0045 (EmployerCostSetup).
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0045_employer_cost_setup'),
    ]

    operations = [
        migrations.CreateModel(
            name='EmployeeDocument',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('document_type', models.CharField(
                    max_length=30,
                    choices=[
                        ('passport',            'Passport'),
                        ('emirates_id',         'Emirates ID'),
                        ('uae_visa',            'UAE Visa'),
                        ('labour_card',         'Labour Card / Work Permit'),
                        ('employment_contract', 'Employment Contract'),
                        ('offer_letter',        'Offer Letter'),
                        ('medical_insurance',   'Medical Insurance Card'),
                        ('educational_cert',    'Educational Certificate'),
                        ('nda',                 'NDA / Non-Disclosure Agreement'),
                        ('bank_document',       'Bank Document'),
                        ('other',               'Other'),
                    ],
                    help_text='Category of document',
                )),
                ('document_number', models.CharField(
                    max_length=100, blank=True,
                    help_text='Passport number, EID number, visa number, etc.',
                )),
                ('issue_date', models.DateField(
                    null=True, blank=True,
                    help_text='Date of issue',
                )),
                ('expiry_date', models.DateField(
                    null=True, blank=True,
                    help_text='Expiry / valid until date — drives alert colouring',
                )),
                ('issuing_country', models.CharField(
                    max_length=100, blank=True,
                    help_text='Country that issued the document',
                )),
                ('file', models.FileField(
                    upload_to='employee_documents/%Y/%m/',
                    null=True, blank=True,
                    help_text='Scanned copy or digital version of the document',
                )),
                ('is_verified', models.BooleanField(
                    default=False,
                    help_text='HR has sighted and verified the original document',
                )),
                ('verified_by', models.CharField(
                    max_length=150, blank=True,
                    help_text='Username of the HR officer who verified',
                )),
                ('verified_at', models.DateTimeField(
                    null=True, blank=True,
                    help_text='Timestamp of verification',
                )),
                ('notes', models.TextField(
                    blank=True,
                    help_text='Any additional notes about this document',
                )),
                ('created_by', models.CharField(
                    max_length=150,
                    help_text='Username who added this record',
                )),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('employee', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='documents',
                    to='attendance.employee',
                    help_text='In-house employee (leave blank for remote)',
                )),
                ('remote_employee', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='documents',
                    to='attendance.remoteemployee',
                    help_text='Remote employee (leave blank for in-house)',
                )),
            ],
            options={
                'verbose_name': 'Employee Document',
                'verbose_name_plural': 'Employee Documents',
                'ordering': ['document_type', '-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='employeedocument',
            index=models.Index(
                fields=['employee', 'document_type'],
                name='att__edoc_emp_type_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='employeedocument',
            index=models.Index(
                fields=['expiry_date'],
                name='att__edoc_expiry_idx',
            ),
        ),
    ]
