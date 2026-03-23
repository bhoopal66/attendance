import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0021_remoteemployee_salary_designation'),
        ('payroll', '0002_payrolladjustment_remote_support'),
    ]

    operations = [
        migrations.CreateModel(
            name='Bank',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100, unique=True)),
                ('per_account_charge', models.DecimalField(
                    decimal_places=2, max_digits=10,
                    help_text='Commission per account submission (AED)'
                )),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='BankSubmission',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('year', models.IntegerField()),
                ('month', models.IntegerField(help_text='1-12')),
                ('submission_count', models.PositiveIntegerField(default=0)),
                ('bank', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='submissions',
                    to='payroll.bank',
                )),
                ('employee', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='bank_submissions',
                    to='attendance.employee',
                )),
                ('remote_employee', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='bank_submissions',
                    to='attendance.remoteemployee',
                )),
            ],
            options={
                'ordering': ['bank__name'],
            },
        ),
        migrations.AddIndex(
            model_name='banksubmission',
            index=models.Index(fields=['employee', 'year', 'month'], name='payroll_sub_emp_ym_idx'),
        ),
        migrations.AddIndex(
            model_name='banksubmission',
            index=models.Index(fields=['remote_employee', 'year', 'month'], name='payroll_sub_remote_ym_idx'),
        ),
    ]
