from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payroll', '0010_rename_visa_cost_remove_categories'),
    ]

    operations = [
        migrations.AddField(
            model_name='bank',
            name='inr_per_account_charge',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text='Commission per account submission (INR) — for Indian employees. Leave blank if not applicable.',
                max_digits=10,
                null=True,
            ),
        ),
    ]
