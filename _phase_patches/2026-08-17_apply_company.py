"""Phase 1a — add the Company model and put a company FK on every employee.

    python 2026-08-17_apply_company.py            # show the diff, change nothing
    python 2026-08-17_apply_company.py --apply    # write it

WHAT IT TOUCHES
    attendance/models.py   append Company; insert company FK on BaseEmployee
    attendance/admin.py    append a registration so entities can be added today

The FK is NULLABLE here on purpose. It is filled by `manage.py seed_company`
and made required by a later migration, because adding a NOT NULL column to a
table with 44 rows in it fails on the first row.

Written without the device bridge, so every anchor is matched exactly first,
then whitespace-tolerantly, and the script REFUSES rather than guesses.
"""
import argparse
import difflib
import io
import os
import re
import sys

MODELS = os.path.join('attendance', 'models.py')
ADMIN = os.path.join('attendance', 'admin.py')

COMPANY_MODEL = '\n\nclass Company(models.Model):\n    """A legal entity. Taamul, NAAS, and whatever comes next.\n\n    ADDED BY THE USER, NOT SEEDED BY A DEVELOPER. bhoopal\'s instruction on\n    17 Aug 2026 was that entities must be addable as and when required, so this\n    ships with no hard-coded list: one default row is created from the existing\n    data and everything after that is entered through the UI.\n\n    Deliberately LEAN. The specification (§82) lists work week, leave rules,\n    payroll rules, approval rules, WPS configuration, gratuity rules, holiday\n    calendar and salary components as per-company settings. None of them are\n    fields here, because each belongs on the table that already owns that\n    concept — a holiday calendar is a property of Holiday, not of Company.\n    Putting them here would create a second place for the same rule to live and\n    a second chance for the two to disagree. They arrive as `company` foreign\n    keys on those tables, one phase at a time.\n    """\n    code = models.CharField(\n        max_length=12, unique=True,\n        help_text="Short handle, e.g. TAM or NAAS. Used in reports and exports. "\n                  "NOT used to build employee numbers — those stay in the TCR "\n                  "format, entered by hand (bhoopal, 17 Aug 2026).",\n    )\n    name = models.CharField(max_length=120, help_text="Trading name as people say it.")\n    legal_name = models.CharField(\n        max_length=200, blank=True, default=\'\',\n        help_text="Full registered name, for contracts and letters. Blank is "\n                  "allowed — an unfilled field is better than a guessed one on "\n                  "a document somebody signs.",\n    )\n    trade_licence_number = models.CharField(max_length=60, blank=True, default=\'\')\n    establishment_number = models.CharField(\n        max_length=60, blank=True, default=\'\',\n        help_text="MOHRE establishment number, where the entity has one.",\n    )\n    default_labour_jurisdiction = models.CharField(\n        max_length=20, blank=True, default=\'\',\n        help_text="Suggested jurisdiction for new employees of this entity. A "\n                  "SUGGESTION, not an override: jurisdiction is decided per "\n                  "employee, because one entity can hold MOHRE staff, own-visa "\n                  "staff and offshore staff at the same time — as this one does.",\n    )\n    is_active = models.BooleanField(default=True)\n    notes = models.TextField(blank=True, default=\'\')\n    created_at = models.DateTimeField(auto_now_add=True)\n    updated_at = models.DateTimeField(auto_now=True)\n\n    class Meta:\n        ordering = [\'name\']\n        verbose_name_plural = \'Companies\'\n\n    def __str__(self):\n        return f\'{self.name} ({self.code})\'\n'

COMPANY_FK = '    company = models.ForeignKey(\n        \'attendance.Company\', on_delete=models.PROTECT,\n        null=True, blank=True, related_name=\'%(class)ss\',\n        help_text="Which legal entity employs this person. NULLABLE for now: "\n                  "the column lands first, every existing employee is assigned "\n                  "to a default entity by the seed_company command, and only "\n                  "then is it made required. Making it required in the same "\n                  "migration that creates it would fail on the first row of a "\n                  "live payroll database. "\n                  "on_delete=PROTECT because deleting a company out from under "\n                  "44 payroll records should be refused, not cascaded.",\n    )\n\n'

FK_ANCHOR_EXACT = '    LABOUR_JURISDICTION_CHOICES = ['
FK_ANCHOR_REGEX = r'^\s*LABOUR_JURISDICTION_CHOICES = \['

ADMIN_BLOCK = """

# --- Phase 1a: legal entities -------------------------------------------------
# Registered so entities can be added from day one, as requested: "I should add
# entities as and when required." A dedicated settings page can replace this
# later; until then the admin is a working door rather than a missing one.
from attendance.models import Company as _Company


@admin.register(_Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'legal_name', 'is_active', 'headcount')
    list_filter = ('is_active',)
    search_fields = ('code', 'name', 'legal_name', 'trade_licence_number')

    def headcount(self, obj):
        # Both employee types, because a company that looks empty when it holds
        # 27 remote staff is worse than no number at all.
        return obj.employees.count() + obj.remoteemployees.count()
    headcount.short_description = 'Employees'
"""


def refuse(msg):
    print('ERROR: ' + msg)
    print('The files are not in the state this patch expects. NOTHING CHANGED.')
    print('Send me the file and I will re-cut the patch against it.')
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    opts = ap.parse_args()

    for path in (MODELS, ADMIN):
        if not os.path.exists(path):
            return refuse('%s not found — run this from the repo root.' % path)

    raw_m = open(MODELS, 'rb').read()
    crlf_m = b'\r\n' in raw_m
    models_src = raw_m.decode('utf-8').replace('\r\n', '\n')

    raw_a = open(ADMIN, 'rb').read()
    crlf_a = b'\r\n' in raw_a
    admin_src = raw_a.decode('utf-8').replace('\r\n', '\n')

    done_m = 'class Company(models.Model)' in models_src
    done_a = 'class CompanyAdmin' in admin_src
    if done_m and done_a:
        print('Already applied — Company and CompanyAdmin are both present.')
        print('Nothing changed. This is the expected result of a second run.')
        return 0

    new_models, new_admin = models_src, admin_src

    if not done_m:
        if 'labour_jurisdiction = models.CharField(' not in models_src:
            return refuse('labour_jurisdiction is not on BaseEmployee yet — '
                          'run 2026-08-17_apply_labour_jurisdiction.py first.')
        hits = models_src.count(FK_ANCHOR_EXACT)
        if hits != 1:
            lines = models_src.split('\n')
            idx = [i for i, l in enumerate(lines) if re.match(FK_ANCHOR_REGEX, l)]
            if len(idx) != 1:
                return refuse('expected one LABOUR_JURISDICTION_CHOICES anchor, found %d.' % len(idx))
            lines[idx[0]:idx[0]] = COMPANY_FK.rstrip('\n').split('\n')
            new_models = '\n'.join(lines)
        else:
            new_models = models_src.replace(FK_ANCHOR_EXACT, COMPANY_FK + FK_ANCHOR_EXACT, 1)
        new_models = new_models.rstrip('\n') + '\n' + COMPANY_MODEL
    else:
        print('note: Company model already present, only admin.py needs the change')

    if not done_a:
        if not re.search(r'^\s*from django\.contrib import admin', admin_src, re.M):
            return refuse('attendance/admin.py does not import django.contrib.admin as expected.')
        new_admin = admin_src.rstrip('\n') + '\n' + ADMIN_BLOCK
    else:
        print('note: CompanyAdmin already present, only models.py needs the change')

    for path, before, after in ((MODELS, models_src, new_models), (ADMIN, admin_src, new_admin)):
        if before == after:
            continue
        print('')
        sys.stdout.writelines(difflib.unified_diff(
            before.splitlines(True), after.splitlines(True),
            fromfile=path, tofile=path + ' (patched)', n=2))

    if not opts.apply:
        print('')
        print('DRY RUN — nothing was written. Re-run with --apply if the diff is right.')
        return 0

    for path, after, crlf in ((MODELS, new_models, crlf_m), (ADMIN, new_admin, crlf_a)):
        out = after.replace('\n', '\r\n') if crlf else after
        with io.open(path, 'wb') as fh:
            fh.write(out.encode('utf-8'))
        print('PATCHED %s (%s)' % (path, 'CRLF' if crlf else 'LF'))

    print('')
    print('Next:')
    print('  copy 0056_company.py  -> attendance/migrations/')
    print('  copy seed_company.py  -> attendance/management/commands/')
    print('  python manage.py makemigrations --check --dry-run attendance   # "No changes detected"')
    print('  python manage.py migrate attendance')
    print('  python manage.py seed_company            # report first')
    return 0


if __name__ == '__main__':
    sys.exit(main())
