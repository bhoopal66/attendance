"""Records the date an employee actually returned from annual leave.

Nullable and additive — every existing row keeps working, and the Sunday
entitlement engine infers `end_date + 1` when this is blank, flagging the
result as inferred rather than presenting it as recorded fact.

It exists because the two are not the same thing. Leave scheduled to end on
the 12th does not mean the employee was back on the 13th; they can return late
or early, and Sunday entitlement restarts from the day they were really back.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0049_rename_attendance__employm_idx_attendance__employe_d26f67_idx_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='annualleave',
            name='actual_rejoining_date',
            field=models.DateField(blank=True, null=True, help_text='The date the employee actually returned to work. Often end_date + 1, but not always — an employee can come back late or early, and Sunday entitlement restarts from the day they were really back, not the day the leave was scheduled to end. Left blank, the Sunday engine infers end_date + 1 and flags the figure as inferred.'),
        ),
    ]
