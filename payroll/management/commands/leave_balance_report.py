"""
Annual leave balance and encashment exposure, per employee.

    DJANGO_SETTINGS_MODULE=attendance_project.settings.production \
        python3 manage.py leave_balance_report
    ... --as-of 2026-08-15 --csv leave.csv --divisor 30

Read only. Runs from the shell, so it works while the web app is down.
"""

import csv
import datetime

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Annual leave accrued, taken, balance and encashment exposure per employee.'

    def add_arguments(self, parser):
        parser.add_argument('--as-of', dest='as_of', default='',
                            help='YYYY-MM-DD (default: today)')
        parser.add_argument('--divisor', type=int, default=30,
                            help='Daily-rate divisor. 30 = UAE leave convention (default). '
                                 'The payroll engine uses days-in-period instead.')
        parser.add_argument('--days-per-year', type=float, default=30.0,
                            help='Entitlement after 1 year (default 30, the statutory minimum)')
        parser.add_argument('--csv', dest='csv_path', default='')
        parser.add_argument('--include-inactive', action='store_true')

    def handle(self, *args, **opts):
        from attendance.models import Employee, RemoteEmployee
        from payroll import services_leave_earnings as svc

        as_of = (datetime.date.fromisoformat(opts['as_of'])
                 if opts['as_of'] else datetime.date.today())
        divisor = opts['divisor']

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'Annual leave balances as at {as_of} (divisor {divisor})'))
        self.stdout.write(
            '  Leave TAKEN is paid at the full wage; leave ENCASHED at basic only '
            '(Art. 29, Decree-Law 33/2021).')
        self.stdout.write('')

        rows = []
        for model in (Employee, RemoteEmployee):
            qs = model.objects.all() if opts['include_inactive'] \
                else model.objects.filter(is_active=True)
            for emp in qs.order_by('name'):
                rows.append(svc.leave_summary(emp, as_of, divisor, opts['days_per_year']))

        hdr = (f'{"Employee":<24}{"Joined":<12}{"Accr":>7}{"Taken":>7}{"Bal":>7}'
               f'{"Full/day":>10}{"Basic/day":>11}{"Encash":>12}  Notes')
        self.stdout.write(hdr)
        self.stdout.write('-' * len(hdr))

        exposure = {}
        negatives = unknown_basic = 0
        for r in rows:
            if r['balance_days'] is not None and r['balance_days'] < 0:
                negatives += 1
            if r['basic'] is None:
                unknown_basic += 1
            if r['encashment_value'] is not None:
                exposure[r['currency']] = exposure.get(r['currency'], 0) + r['encashment_value']
            self.stdout.write(
                f'{r["name"][:23]:<24}'
                f'{(r["joining_date"].isoformat() if r["joining_date"] else "—"):<12}'
                f'{self._n(r["accrued_days"]):>7}{self._n(r["taken_days"]):>7}'
                f'{self._n(r["balance_days"]):>7}'
                f'{self._m(r["day_rate_full"]):>10}{self._m(r["day_rate_basic"]):>11}'
                f'{self._m(r["encashment_value"]):>12}  '
                f'{"; ".join(r["notes"])[:70]}')

        self.stdout.write('')
        self.stdout.write(f'  {len(rows)} employees')
        for cur, amt in sorted(exposure.items()):
            self.stdout.write(self.style.WARNING(
                f'  Encashment exposure {cur} {amt:,.2f} — what the untaken balance '
                f'would cost if everyone left tomorrow, at the company rate of '
                f'{svc.POLICY_LEAVE_PCT:g}% of gross'))
        _short = {}
        for r in rows:
            if r.get('encashment_shortfall'):
                _short[r['currency']] = (_short.get(r['currency'], 0)
                                         + r['encashment_shortfall'])
        for cur, amt in sorted(_short.items()):
            self.stdout.write(self.style.ERROR(
                f'  Below the Article 29 floor by {cur} {amt:,.2f} in total — leave '
                f'encashed on termination is payable at basic salary, 100%. This is '
                f'the gap between what the policy pays and that floor.'))
        if unknown_basic:
            self.stdout.write(self.style.WARNING(
                f'  {unknown_basic} employees have NO known basic salary. They ARE '
                f'in the exposure above — it is computed from gross — but their '
                f'Article 29 comparison could not be made, so a shortfall against '
                f'the statutory floor cannot be ruled out for them.'))
        if negatives:
            self.stdout.write(self.style.ERROR(
                f'  {negatives} employees have taken MORE leave than they have accrued.'))
        if not rows:
            self.stdout.write(self.style.WARNING('  No employees matched — nothing was checked.'))

        if opts['csv_path']:
            with open(opts['csv_path'], 'w', newline='', encoding='utf-8') as fh:
                w = csv.writer(fh)
                w.writerow(['Name', 'TCR', 'Type', 'Currency', 'Joined', 'Months service',
                            'Accrued days', 'Taken days', 'Balance days', 'Full wage',
                            'Basic', 'Wage source', 'Full/day', 'Basic/day',
                            'Encashment value', 'Notes'])
                for r in rows:
                    w.writerow([r['name'], r['tcr'], r['employee_type'], r['currency'],
                                r['joining_date'] or '',
                                '' if r['months_service'] is None else round(r['months_service'], 1),
                                r['accrued_days'], r['taken_days'], r['balance_days'],
                                r['full_wage'], r['basic'] if r['basic'] is not None else '',
                                r['wage_source'], r['day_rate_full'],
                                r['day_rate_basic'] if r['day_rate_basic'] is not None else '',
                                r['encashment_value'] if r['encashment_value'] is not None else '',
                                '; '.join(r['notes'])])
            self.stdout.write(f'\n  Written to {opts["csv_path"]}')

    @staticmethod
    def _n(v):
        return '—' if v is None else f'{v:g}'

    @staticmethod
    def _m(v):
        return '—' if v is None else f'{v:,.2f}'
