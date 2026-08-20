import datetime

from django.db import migrations, models


def forwards_populate_dates(apps, schema_editor):
    """Convert existing (effective_year, effective_month) rows to effective_date.

    Uses the 1st of that month — the best available approximation for rows
    created before day-level precision existed. These were all created
    during this feature's initial testing, so there is no real payroll
    history to protect, but we convert rather than discard on principle.
    """
    SalaryCycleDefault = apps.get_model('attendance', 'SalaryCycleDefault')
    SalaryCycleHistory = apps.get_model('attendance', 'SalaryCycleHistory')

    for row in SalaryCycleDefault.objects.all():
        row.effective_date = datetime.date(row.effective_year, row.effective_month, 1)
        row.save(update_fields=['effective_date'])

    for row in SalaryCycleHistory.objects.all():
        row.effective_date = datetime.date(row.effective_year, row.effective_month, 1)
        row.save(update_fields=['effective_date'])


def backwards_populate_period(apps, schema_editor):
    SalaryCycleDefault = apps.get_model('attendance', 'SalaryCycleDefault')
    SalaryCycleHistory = apps.get_model('attendance', 'SalaryCycleHistory')

    for row in SalaryCycleDefault.objects.all():
        row.effective_year = row.effective_date.year
        row.effective_month = row.effective_date.month
        row.save(update_fields=['effective_year', 'effective_month'])

    for row in SalaryCycleHistory.objects.all():
        row.effective_year = row.effective_date.year
        row.effective_month = row.effective_date.month
        row.save(update_fields=['effective_year', 'effective_month'])


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0062_salary_cycle_history'),
    ]

    operations = [
        # 1. Add the new field, nullable for now so existing rows don't need
        #    a default at the DB level — the data migration fills it in.
        migrations.AddField(
            model_name='salarycycledefault',
            name='effective_date',
            field=models.DateField(null=True, help_text='Exact date this cycle takes effect (inclusive)'),
        ),
        migrations.AddField(
            model_name='salarycyclehistory',
            name='effective_date',
            field=models.DateField(null=True, help_text='Exact date this override takes effect (inclusive)'),
        ),

        # 2. Populate it from the old (year, month) columns.
        migrations.RunPython(forwards_populate_dates, backwards_populate_period),

        # 3. Drop the old unique constraints (they reference columns we're
        #    about to remove).
        migrations.RemoveConstraint(
            model_name='salarycycledefault',
            name='uniq_salary_cycle_default_period',
        ),
        migrations.RemoveConstraint(
            model_name='salarycyclehistory',
            name='uniq_salary_cycle_emp_period',
        ),
        migrations.RemoveConstraint(
            model_name='salarycyclehistory',
            name='uniq_salary_cycle_remote_period',
        ),

        # 4. Remove the old columns.
        migrations.RemoveField(model_name='salarycycledefault', name='effective_year'),
        migrations.RemoveField(model_name='salarycycledefault', name='effective_month'),
        migrations.RemoveField(model_name='salarycyclehistory', name='effective_year'),
        migrations.RemoveField(model_name='salarycyclehistory', name='effective_month'),

        # 5. Make effective_date required now that every row has one, and
        #    add the new unique constraints + ordering/verbose_name.
        migrations.AlterField(
            model_name='salarycycledefault',
            name='effective_date',
            field=models.DateField(help_text='Exact date this cycle takes effect (inclusive)'),
        ),
        migrations.AlterField(
            model_name='salarycyclehistory',
            name='effective_date',
            field=models.DateField(help_text='Exact date this override takes effect (inclusive)'),
        ),
        migrations.AlterModelOptions(
            name='salarycycledefault',
            options={'ordering': ['-effective_date'], 'verbose_name': 'Salary Cycle Default', 'verbose_name_plural': 'Salary Cycle Defaults'},
        ),
        migrations.AlterModelOptions(
            name='salarycyclehistory',
            options={'ordering': ['-effective_date'], 'verbose_name': 'Salary Cycle History', 'verbose_name_plural': 'Salary Cycle Histories'},
        ),
        migrations.AddConstraint(
            model_name='salarycycledefault',
            constraint=models.UniqueConstraint(fields=('effective_date',), name='uniq_salary_cycle_default_date'),
        ),
        migrations.AddConstraint(
            model_name='salarycyclehistory',
            constraint=models.UniqueConstraint(condition=models.Q(('employee__isnull', False)), fields=('employee', 'effective_date'), name='uniq_salary_cycle_emp_date'),
        ),
        migrations.AddConstraint(
            model_name='salarycyclehistory',
            constraint=models.UniqueConstraint(condition=models.Q(('remote_employee__isnull', False)), fields=('remote_employee', 'effective_date'), name='uniq_salary_cycle_remote_date'),
        ),
    ]
