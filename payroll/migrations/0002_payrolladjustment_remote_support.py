import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0021_remoteemployee_salary_designation'),
        ('payroll', '0001_initial'),
    ]

    operations = [
        # Make employee nullable so remote-only adjustments are valid
        migrations.AlterField(
            model_name='payrolladjustment',
            name='employee',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='payroll_adjustments',
                to='attendance.employee',
            ),
        ),
        # Add remote_employee FK
        migrations.AddField(
            model_name='payrolladjustment',
            name='remote_employee',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='payroll_adjustments',
                to='attendance.remoteemployee',
            ),
        ),
        # Replace the old index with two targeted ones
        migrations.RemoveIndex(
            model_name='payrolladjustment',
            name='payroll_pay_employe_265fbb_idx',
        ),
        migrations.AddIndex(
            model_name='payrolladjustment',
            index=models.Index(fields=['employee', 'year', 'month'], name='payroll_adj_emp_ym_idx'),
        ),
        migrations.AddIndex(
            model_name='payrolladjustment',
            index=models.Index(fields=['remote_employee', 'year', 'month'], name='payroll_adj_remote_ym_idx'),
        ),
    ]
