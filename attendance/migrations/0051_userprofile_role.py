from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0050_annualleave_actual_rejoining_date'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='role',
            field=models.CharField(
                blank=True, db_index=True, default='', max_length=20,
                choices=[
                    ('', '— none —'),
                    ('hr_admin', 'HR Admin'),
                    ('exec_director', 'Executive Director'),
                    ('manager', 'Manager'),
                    ('it', 'IT'),
                ],
                help_text=(
                    'Business role. Governs which employee compliance fields this user '
                    'may see, separately from which pages they may open. A user with no '
                    'role sees no identity numbers, no bank details and no commission '
                    'fields, however many sidebar sections they have been granted.'
                ),
            ),
        ),
    ]
