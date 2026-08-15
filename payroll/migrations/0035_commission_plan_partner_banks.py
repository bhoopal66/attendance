import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payroll', '0034_payrollrun_reopen'),
        # Needs Employee / RemoteEmployee to exist, and 0052 is where the
        # compliance block lands — keeping them ordered means a half-applied
        # deploy cannot leave partner banks pointing at a model without the
        # fields that give them meaning.
        ('attendance', '0052_compliance_fields'),
    ]

    operations = [
        migrations.CreateModel(
            name='CommissionPlan',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('code', models.CharField(
                    max_length=30, unique=True,
                    help_text='Short code stored on the employee, e.g. DSA-STD-2026')),
                ('name', models.CharField(max_length=120,
                                          help_text='Human-readable plan name')),
                ('description', models.TextField(blank=True)),
                ('is_active', models.BooleanField(
                    default=True,
                    help_text='Inactive plans stay on employees already using them '
                              'but cannot be picked for anyone new.')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Commission Plan',
                'verbose_name_plural': 'Commission Plans',
                'ordering': ['code'],
            },
        ),
        migrations.CreateModel(
            name='EmployeePartnerBank',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('is_primary', models.BooleanField(
                    default=False,
                    help_text='The bank this employee is chiefly targeted against.')),
                ('assigned_at', models.DateTimeField(auto_now_add=True)),
                ('assigned_by', models.CharField(blank=True, default='', max_length=150)),
                ('bank', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='assigned_employees', to='payroll.bank',
                    help_text='PROTECT, not CASCADE: deleting a bank must not '
                              'silently unassign the people working it.')),
                ('employee', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='partner_banks', to='attendance.employee',
                    help_text='In-house employee (leave blank for remote)')),
                ('remote_employee', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='partner_banks', to='attendance.remoteemployee',
                    help_text='Remote employee (leave blank for in-house)')),
            ],
            options={
                'verbose_name': 'Employee Partner Bank',
                'verbose_name_plural': 'Employee Partner Banks',
            },
        ),
        migrations.AddIndex(
            model_name='employeepartnerbank',
            index=models.Index(fields=['employee'], name='pay_epb_emp_idx'),
        ),
        migrations.AddIndex(
            model_name='employeepartnerbank',
            index=models.Index(fields=['remote_employee'], name='pay_epb_remote_idx'),
        ),
        migrations.AddConstraint(
            model_name='employeepartnerbank',
            constraint=models.UniqueConstraint(
                condition=models.Q(('employee__isnull', False)),
                fields=('employee', 'bank'), name='uniq_inhouse_partner_bank'),
        ),
        migrations.AddConstraint(
            model_name='employeepartnerbank',
            constraint=models.UniqueConstraint(
                condition=models.Q(('remote_employee__isnull', False)),
                fields=('remote_employee', 'bank'), name='uniq_remote_partner_bank'),
        ),
    ]
