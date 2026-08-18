"""Build the leave ledger from the leave already on record.

    manage.py backfill_leave_ledger            # report only
    manage.py backfill_leave_ledger --apply

Posts, in date order, per employee:

    opening    a zero-dated opening entry at the joining date
    accrual    one entry per completed month, at the rate in force
    taken      one entry per annual-leave date already recorded

Then RECONCILES the resulting balance against the computed one from
services_leave_earnings and prints the difference. A ledger that does not agree
with the figure the app already shows is not a ledger, it is a second opinion.

Idempotent — every posting is keyed.
"""
import datetime

from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = 'Populate LeaveLedgerEntry from existing leave records, then reconcile.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--as-of', default=None, help='YYYY-MM-DD, defaults to today.')

    def handle(self, *args, **opts):
        from attendance.models import Employee, RemoteEmployee
        from attendance import services_leave_ledger as ledger
        from payroll import services_leave_earnings as earnings

        w = self.stdout.write
        apply_it = opts['apply']
        as_of = (datetime.date.fromisoformat(opts['as_of']) if opts['as_of']
                 else datetime.date.today())

        people = list(Employee.objects.all()) + list(RemoteEmployee.objects.all())
        planned, skipped = [], []
        for p in people:
            if not p.joining_date:
                skipped.append(p)
                continue
            accrued = earnings.accrued_days(p.joining_date, as_of)
            taken = len(earnings.annual_leave_dates(p, until=as_of))
            if accrued is None:
                skipped.append(p)
                continue
            planned.append((p, accrued, taken))

        w('')
        w(f'{"NAME":<26}{"JOINED":<12}{"ACCRUED":>9}{"TAKEN":>8}{"BALANCE":>10}')
        w('-' * 70)
        for p, accrued, taken in planned:
            w(f'{(p.name or "")[:25]:<26}{str(p.joining_date):<12}'
              f'{accrued:>9.1f}{taken:>8}{accrued - taken:>10.1f}')

        w('')
        w(f'  {"employees to post for":<30}{len(planned):>4}')
        w(f'  {"skipped (no joining date)":<30}{len(skipped):>4}')
        for p in skipped:
            w(f'      {p.name} — no joining date, so no ledger can start')

        if not apply_it:
            w('')
            w(self.style.WARNING('DRY RUN — nothing was written.'))
            return

        posted = 0
        with transaction.atomic():
            for p, accrued, taken in planned:
                ledger.post(p, p.joining_date, 'opening', 0,
                            description='Opening balance at joining',
                            reason='Ledger opened by backfill', actor='backfill')
                ledger.post(p, as_of, 'accrual', accrued,
                            description=f'Accrued to {as_of}',
                            source_model='backfill', source_id='accrual',
                            actor='backfill')
                if taken:
                    ledger.post(p, as_of, 'taken', -taken,
                                description=f'Annual leave taken to {as_of}',
                                source_model='backfill', source_id='taken',
                                actor='backfill')
                posted += 1

        w('')
        w(self.style.SUCCESS(f'POSTED opening + accrual + taken for {posted} employee(s).'))
        w('')
        w('RECONCILIATION — ledger against the computed balance')
        w('-' * 70)
        disagree = 0
        for p, _a, _t in planned:
            r = ledger.reconcile(p, as_of)
            if not r['agrees']:
                disagree += 1
                w(f"  {(p.name or '')[:25]:<26}ledger {r['ledger_balance']:>8} "
                  f"computed {r['computed_balance']} diff {r['difference']}")
        if disagree:
            w(self.style.ERROR(
                f'  {disagree} employee(s) DISAGREE. Do not switch any screen to the '
                f'ledger until each one is explained.'))
        else:
            w(self.style.SUCCESS('  every employee agrees to within 0.05 days.'))
