"""Align four index names with what Django computes from the models.

WHY THIS EXISTS
---------------
`makemigrations --check` reported drift: the index names hand-written into
0030 (loans) and 0033 (Sunday entitlement) do not match the hash Django
derives from the model definition. Nothing is broken by that — the indexes
exist and work, and `migrate` applies cleanly either way — but model state and
migration state disagree, so the next person to run `makemigrations` gets a
surprise migration they did not ask for and will not trust.

Renames only. No column, table, constraint or datum is touched. The four
tables involved are small, so the DDL is momentary.

After this, `makemigrations --check` is clean for both apps.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("payroll", "0035_commission_plan_partner_banks"),
    ]

    operations = [
        migrations.RenameIndex(
            model_name="loan",
            new_name="payroll_loa_employe_3dc6d9_idx",
            old_name="payroll_loa_employe_2b1f3c_idx",
        ),
        migrations.RenameIndex(
            model_name="loan",
            new_name="payroll_loa_remote__8dcc1a_idx",
            old_name="payroll_loa_remote__7d4a91_idx",
        ),
        migrations.RenameIndex(
            model_name="loaninstallment",
            new_name="payroll_loa_year_f3c868_idx",
            old_name="payroll_loa_year_c5e802_idx",
        ),
        migrations.RenameIndex(
            model_name="sundayentitlementrecord",
            new_name="payroll_sun_year_4a4446_idx",
            old_name="payroll_sun_year_8c3d21_idx",
        ),
    ]
