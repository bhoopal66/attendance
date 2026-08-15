"""
Phase 0 — payroll regression harness.

Records exactly what the payroll calculation produces today, so that any later
change which alters a computed figure is caught immediately instead of being
discovered in someone's salary.

    # capture the current behaviour as the baseline
    python manage.py payroll_snapshot --write --year 2026 --month 7

    # later — after refactoring — prove nothing moved
    python manage.py payroll_snapshot --check --year 2026 --month 7

`--check` exits 1 on any drift and prints a per-employee, per-field diff.

WHY A MANAGEMENT COMMAND AND NOT A DJANGO TestCase
--------------------------------------------------
Django's test runner builds an *empty* test database. A regression harness for
payroll has to run against the real data — the whole point is to prove that
real employees' real figures did not move. A TestCase would happily pass
against zero employees and tell you nothing. So this is a command you point at
the live database (read-only), and the golden file is committed to the repo.

WHAT THIS DOES AND DOES NOT PROVE
---------------------------------
It compares OUTPUT, not code. It cannot tell a code change from a data change:
if somebody edits an attendance record between --write and --check, that shows
up as drift, correctly, because the figure really did change. When using this
to validate a refactor, run --write and --check close together and do not touch
payroll data in between. The `source_data_fingerprint` in the golden file
records the row counts of the inputs, so an obvious data change is at least
visible rather than silent.

Deliberately exercises the real calculation via
`payroll.services_payroll_engine`, rather than reimplementing the maths here.
If someone changes how payroll is computed, this harness must break — that is
its only job. A harness that recomputed payroll its own way would merely agree
with itself and prove nothing.

READ-ONLY: opens no transaction and writes nothing to the database.
"""

import json
import os
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError


# Keys carrying a model instance or other non-serialisable object. Excluded from
# the snapshot body; employee identity is captured separately and explicitly.
_SKIP_KEYS = {
    'employee', 'paid_snapshot', 'paid_snapshot_json', 'ded_breakdown_json',
    'paid_at', 'payment_date', 'bank_counts_list',
}


def _jsonable(value):
    """Reduce a computed value to something stable and comparable.

    Decimals become floats (the calculation is float-based today; Phase 1 may
    change that, and this harness should flag it if the *result* moves).
    Unknown objects become their repr so a type change is visible rather than
    crashing the run.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        # Guard against float noise producing spurious drift while still
        # catching any change a payroll figure could meaningfully have.
        return round(value, 4)
    if isinstance(value, Decimal):
        return round(float(value), 4)
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in sorted(value.items())}
    return repr(value)


def _row_key(row):
    """Stable identity for a computed row, independent of database ids."""
    emp = row.get('employee')
    emp_type = row.get('employee_type', '?')
    tcr = (getattr(emp, 'tcr_id', '') or '').strip()
    name = (getattr(emp, 'name', '') or '').strip()
    ident = tcr or f'id{getattr(emp, "id", "?")}'
    return f'{emp_type}:{ident}:{name}'


def _serialise(row):
    return {k: _jsonable(v) for k, v in sorted(row.items()) if k not in _SKIP_KEYS}


class Command(BaseCommand):
    help = 'Capture or verify a payroll calculation baseline (Phase 0 regression harness).'

    def add_arguments(self, parser):
        parser.add_argument('--year', type=int, required=True)
        parser.add_argument('--month', type=int, required=True, help='1-12')
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument('--write', action='store_true', help='Save the current output as the baseline.')
        group.add_argument('--check', action='store_true', help='Compare current output against the baseline.')
        parser.add_argument(
            '--dir', default=None,
            help='Where baselines live (default: payroll/regression_baselines/).',
        )

    # ---- collection ------------------------------------------------------

    def _collect(self, year, month):
        """Rebuild every payroll row for the month.

        Delegates to `payroll.services_payroll_engine.build_all_sections`, which
        is the single definition of "what this month's payroll is". Deliberately
        NOT a second copy of the section-building logic: two copies drift, and a
        harness that drifts from the thing it guards is worse than no harness.

        The engine currently delegates to the `payroll/views.py` implementations,
        so this still exercises the real calculation — which is the point.
        """
        from payroll.services_payroll_engine import build_all_sections

        payload = {}
        for name, rows in build_all_sections(year, month).items():
            payload[name] = {_row_key(r): _serialise(r) for r in rows}
        return payload

    def _fingerprint(self, year, month):
        """Row counts of the inputs, so an obvious data change is visible."""
        from attendance.models import AttendanceRecord, Employee, RemoteEmployee
        from payroll.models import BankSubmission, DeductionEntry
        return {
            'active_inhouse': Employee.objects.filter(is_active=True).count(),
            'active_remote': RemoteEmployee.objects.filter(is_active=True).count(),
            'deduction_entries': DeductionEntry.objects.count(),
            'bank_submissions': BankSubmission.objects.filter(year=year, month=month).count(),
            'attendance_records': AttendanceRecord.objects.count(),
        }

    # ---- entry point -----------------------------------------------------

    def handle(self, *args, **opts):
        year, month = opts['year'], opts['month']
        if not 1 <= month <= 12:
            raise CommandError('--month must be 1-12')

        # NB: deliberately NOT payroll/tests/ — `payroll/tests.py` already exists
        # as a module, and adding a same-named package would make the import
        # ambiguous and break `manage.py test`.
        base = opts['dir'] or os.path.join(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))), 'regression_baselines')
        path = os.path.join(base, f'payroll_{year}_{month:02d}.json')

        self.stdout.write(f'Computing payroll for {month:02d}/{year} …')
        current = {'period': {'year': year, 'month': month},
                   'source_data_fingerprint': self._fingerprint(year, month),
                   'sections': self._collect(year, month)}
        total = sum(len(v) for v in current['sections'].values())
        self.stdout.write(f'  {total} employee rows across {len(current["sections"])} sections')

        if opts['write']:
            os.makedirs(base, exist_ok=True)
            with open(path, 'w', encoding='utf-8') as fh:
                json.dump(current, fh, indent=1, sort_keys=True)
            self.stdout.write(self.style.SUCCESS(f'Baseline written: {path}'))
            self.stdout.write('Commit this file. Re-run with --check after any payroll change.')
            return

        # --check
        if not os.path.exists(path):
            raise CommandError(f'No baseline at {path}. Run --write first.')
        with open(path, encoding='utf-8') as fh:
            golden = json.load(fh)

        if golden.get('source_data_fingerprint') != current['source_data_fingerprint']:
            self.stdout.write(self.style.WARNING(
                'NOTE: source data changed since the baseline was written — differences below '
                'may be data, not code.'))
            for k, was in (golden.get('source_data_fingerprint') or {}).items():
                now = current['source_data_fingerprint'].get(k)
                if was != now:
                    self.stdout.write(f'    {k}: {was} -> {now}')

        drift, missing, added = [], [], []
        for section, rows in current['sections'].items():
            grows = golden.get('sections', {}).get(section, {})
            for key in sorted(set(grows) | set(rows)):
                if key not in rows:
                    missing.append(f'{section}/{key}')
                    continue
                if key not in grows:
                    added.append(f'{section}/{key}')
                    continue
                for field in sorted(set(grows[key]) | set(rows[key])):
                    was, now = grows[key].get(field, '<absent>'), rows[key].get(field, '<absent>')
                    if was != now:
                        drift.append((section, key, field, was, now))

        if not drift and not missing and not added:
            self.stdout.write(self.style.SUCCESS(
                f'PASS — all {total} rows identical to the baseline.'))
            return

        if missing:
            self.stdout.write(self.style.ERROR(f'\n{len(missing)} row(s) gone from the calculation:'))
            for m in missing[:20]:
                self.stdout.write(f'    {m}')
        if added:
            self.stdout.write(self.style.WARNING(f'\n{len(added)} new row(s):'))
            for a in added[:20]:
                self.stdout.write(f'    {a}')
        if drift:
            self.stdout.write(self.style.ERROR(f'\n{len(drift)} changed field(s):'))
            for section, key, field, was, now in drift[:60]:
                self.stdout.write(f'    {section} / {key}\n        {field}: {was}  ->  {now}')
            if len(drift) > 60:
                self.stdout.write(f'    … and {len(drift) - 60} more')

        raise CommandError('FAIL — payroll output differs from the baseline.')
