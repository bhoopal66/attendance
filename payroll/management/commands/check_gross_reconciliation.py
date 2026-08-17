"""
Does Employee.salary agree with the salary structure it is supposed to mirror?

WHY THIS COMMAND EXISTS
-----------------------
Two features price a day of pay, and they take "gross" from different places:

    paid leave    (payroll/views.py)              gross = Employee.salary
    paid holiday  (services_paid_holidays.py)     gross = basic + housing
                                                        + transport + phone
                                                        + other_allowance

Both then divide by the days in the pay period. So while the two grosses
agree, a paid holiday day and a paid leave day are the same size — which is
the promise the whole design rests on: a day paid equals a day deducted.

The moment one employee's `salary` field drifts from their approved
SalaryStructure, that promise quietly breaks for that person only, and
nothing in the application says so. `Employee.salary` is supposed to be kept
in sync by the Salary tab save handler; "supposed to" is not a control.

READ ONLY. This command changes nothing. It tells you who to look at.

Usage
-----
    DJANGO_SETTINGS_MODULE=attendance_project.settings.production \
        python3 manage.py check_gross_reconciliation
    ... --year 2026 --month 1
    ... --csv /tmp/gross_reconciliation.csv
    ... --tolerance 0.01

Exit code is 1 when any mismatch is found, so it can be wired into a
pre-payroll check that actually stops something.
"""

import calendar
import csv
import datetime
from decimal import Decimal

from django.core.management.base import BaseCommand

CENT = Decimal('0.01')
ZERO = Decimal('0.00')


class Command(BaseCommand):
    help = ("Compare Employee.salary against the approved SalaryStructure "
            "component sum, and show the per-day impact on both formulas.")

    def add_arguments(self, parser):
        today = datetime.date.today()
        parser.add_argument('--year', type=int, default=today.year,
                            help='Payroll year (default: current)')
        parser.add_argument('--month', type=int, default=today.month,
                            help='Payroll month 1-12 (default: current)')
        parser.add_argument('--tolerance', type=float, default=0.01,
                            help='Difference below this is ignored (default 0.01)')
        parser.add_argument('--csv', dest='csv_path', default='',
                            help='Also write the full comparison to this CSV path')
        parser.add_argument('--all', action='store_true',
                            help='List every employee, not only mismatches')

    def handle(self, *args, **opts):
        from attendance.models import Employee, RemoteEmployee
        # The real helper, not a reimplementation. A second copy of "which
        # structure applies" would be one more thing that can disagree.
        from payroll.views import get_effective_salary_structure
        from payroll.services_payroll_engine import get_pay_period

        year, month = opts['year'], opts['month']
        if not 1 <= month <= 12:
            self.stderr.write(self.style.ERROR('Month must be 1-12'))
            return
        tol = Decimal(str(opts['tolerance']))

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'Gross reconciliation — {month:02d}/{year}'))

        rows = []
        mismatches = 0
        no_structure = 0

        for emp in Employee.objects.filter(is_active=True).order_by('name'):
            period = get_pay_period(emp, year, month)
            period_days = period.days or 0

            structure = get_effective_salary_structure(emp, period.end)
            flat = Decimal(str(emp.salary or 0)).quantize(CENT)

            if structure is None:
                no_structure += 1
                rows.append({
                    'name': emp.name, 'tcr': emp.tcr_id or '',
                    'department': emp.department or '', 'currency': emp.currency or 'AED',
                    'flat_salary': flat, 'structure_gross': None, 'difference': None,
                    'period_days': period_days,
                    'leave_day_rate': self._rate(flat, period_days),
                    'holiday_day_rate': None,
                    'per_day_gap': None,
                    'status': 'no approved structure',
                })
                continue

            components = (Decimal(str(structure.basic or 0))
                          + Decimal(str(structure.housing or 0))
                          + Decimal(str(structure.transport or 0))
                          + Decimal(str(structure.phone or 0))
                          + Decimal(str(structure.other_allowance or 0)))
            components = components.quantize(CENT)
            diff = (flat - components).quantize(CENT)

            leave_rate = self._rate(flat, period_days)
            holiday_rate = self._rate(components, period_days)
            per_day_gap = ((leave_rate - holiday_rate).quantize(CENT)
                           if leave_rate is not None and holiday_rate is not None else None)

            ok = abs(diff) <= tol
            if not ok:
                mismatches += 1

            rows.append({
                'name': emp.name, 'tcr': emp.tcr_id or '',
                'department': emp.department or '', 'currency': emp.currency or 'AED',
                'flat_salary': flat, 'structure_gross': components, 'difference': diff,
                'period_days': period_days,
                'leave_day_rate': leave_rate, 'holiday_day_rate': holiday_rate,
                'per_day_gap': per_day_gap,
                'status': 'ok' if ok else 'MISMATCH',
            })

        # ---- report -----------------------------------------------------
        shown = rows if opts['all'] else [
            r for r in rows if r['status'] != 'ok']

        if shown:
            self.stdout.write('')
            self.stdout.write(
                f'{"Employee":<26}{"Dept":<10}{"salary":>12}{"structure":>12}'
                f'{"diff":>10}{"leave/day":>11}{"holiday/day":>13}{"gap/day":>10}'
                f'  {"why"}')
            self.stdout.write('-' * 126)
            for r in shown:
                # The status column is not decoration. Without it a
                # no-structure row prints as a line of dashes and reads like a
                # bug in this command rather than a fact about the employee.
                self.stdout.write(
                    f'{r["name"][:25]:<26}{r["department"][:9]:<10}'
                    f'{self._fmt(r["flat_salary"]):>12}'
                    f'{self._fmt(r["structure_gross"]):>12}'
                    f'{self._fmt(r["difference"]):>10}'
                    f'{self._fmt(r["leave_day_rate"]):>11}'
                    f'{self._fmt(r["holiday_day_rate"]):>13}'
                    f'{self._fmt(r["per_day_gap"]):>10}'
                    f'  {r["status"]}')

        self.stdout.write('')
        total = len(rows)
        clean = total - mismatches - no_structure
        self.stdout.write(f'  {total} active in-house employees checked')
        self.stdout.write(f'  {clean} agree')
        if no_structure:
            self.stdout.write(self.style.WARNING(
                f'  {no_structure} have NO approved salary structure — paid holiday '
                f'skips them entirely, paid leave still pays from Employee.salary. '
                f'That is a real difference in treatment, not a rounding gap.'))
        if mismatches:
            self.stdout.write(self.style.ERROR(
                f'  {mismatches} MISMATCH — for these people a paid holiday day and '
                f'a paid leave day are different sizes.'))
        elif clean:
            self.stdout.write(self.style.SUCCESS(
                f'  No mismatches among the {clean} that could be compared.'))
        else:
            # "No mismatches" over zero comparisons is a lie by omission. If
            # nothing could be checked, the honest report is that nothing was
            # checked — not a clean bill of health.
            self.stdout.write(self.style.WARNING(
                '  NOTHING WAS VERIFIED — no employee had a structure to compare '
                'against. This is not a pass.'))

        remote_count = RemoteEmployee.objects.filter(is_active=True).count()
        if remote_count:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING(
                f'  {remote_count} active remote employees were NOT checked — '
                f'RemoteEmployee has no SalaryStructure at all, so there is nothing '
                f'to reconcile against. Their gross is Employee.salary by definition.'))

        # ---- csv --------------------------------------------------------
        if opts['csv_path']:
            with open(opts['csv_path'], 'w', newline='', encoding='utf-8') as fh:
                w = csv.writer(fh)
                w.writerow(['Name', 'TCR', 'Department', 'Currency',
                            'Employee.salary', 'Structure gross', 'Difference',
                            'Period days', 'Paid leave per day',
                            'Paid holiday per day', 'Gap per day', 'Status'])
                for r in rows:
                    w.writerow([
                        r['name'], r['tcr'], r['department'], r['currency'],
                        self._fmt(r['flat_salary']), self._fmt(r['structure_gross']),
                        self._fmt(r['difference']), r['period_days'],
                        self._fmt(r['leave_day_rate']), self._fmt(r['holiday_day_rate']),
                        self._fmt(r['per_day_gap']), r['status'],
                    ])
            self.stdout.write(f'\n  Written to {opts["csv_path"]}')

        if mismatches:
            raise SystemExit(1)

    # ---- helpers --------------------------------------------------------

    @staticmethod
    def _rate(gross, period_days):
        """Gross / days in period — the divisor both formulas use.

        Returns None rather than 0 when the period cannot be resolved: a rate
        of zero would read as "this day is worth nothing", which is a
        different and much worse statement than "unknown".
        """
        if gross is None or not period_days:
            return None
        return (Decimal(gross) / Decimal(period_days)).quantize(CENT)

    @staticmethod
    def _fmt(value):
        return '—' if value is None else f'{value:,.2f}'
