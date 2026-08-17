"""
Explain one employee's month, number by number.

    DJANGO_SETTINGS_MODULE=attendance_project.settings.production \
        python3 manage.py explain_payroll_row --tcr TCR1000224 --year 2026 --month 1

WHY THIS EXISTS
---------------
"Paid leave is not calculated" has two completely different causes and the
payroll screen cannot tell them apart:

  A. the leave WAS deducted, and nothing added it back
       -> paid leave needs to be added, exactly like a paid holiday
  B. the leave was NEVER deducted, because the engine already excluded it
       -> adding it would pay the same day twice

Same symptom on screen. Opposite fix. This prints which one is true, with the
figures, so the decision is made from the data rather than from the symptom.

Read only.
"""

import datetime

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Show how one employee's payroll month was calculated, line by line."

    def add_arguments(self, parser):
        parser.add_argument('--tcr', required=True, help='TCR id, e.g. TCR1000224')
        parser.add_argument('--year', type=int, required=True)
        parser.add_argument('--month', type=int, required=True)

    def handle(self, *args, **opts):
        from attendance.models import (AnnualLeave, AttendanceRecord, Employee,
                                       Holiday, LeaveRequest, RemoteEmployee)
        from payroll.services_payroll_engine import calculate_employee_payroll, get_pay_period

        tcr, year, month = opts['tcr'].strip(), opts['year'], opts['month']

        emp = Employee.objects.filter(tcr_id=tcr).first()
        kind = 'inhouse'
        if emp is None:
            emp = RemoteEmployee.objects.filter(tcr_id=tcr).first()
            kind = 'remote'
        if emp is None:
            self.stderr.write(self.style.ERROR(f'No employee with tcr_id {tcr}'))
            return

        period = get_pay_period(emp, year, month)
        w = self.stdout.write
        w(self.style.MIGRATE_HEADING(
            f'{emp.name}  ({tcr}, {kind}, {emp.department or "no dept"})  —  {month:02d}/{year}'))
        w(f'  pay period      {period.start} .. {period.end}   ({period.days} days)')
        w(f'  payroll type    {getattr(emp, "payroll_type", "attendance")}'
          f'{"   fixed salary" if getattr(emp, "is_fixed_salary", False) else ""}')
        w(f'  gross on record AED {float(emp.salary or 0):,.2f}')
        w('')

        # ---- what the engine produced -----------------------------------
        row = calculate_employee_payroll(emp, kind, year, month)
        def g(k, d=0):
            v = row.get(k, d)
            return d if v is None else v

        w(self.style.MIGRATE_LABEL('  ENGINE OUTPUT'))
        for label, key in (('salary', 'salary'), ('daily rate', 'daily_rate'),
                           ('present / full days', 'full_days'),
                           ('half days', 'half_days'),
                           ('absent days', 'absent_days'),
                           ('late days', 'late_days'),
                           ('paid leave days', 'paid_leave_days'),
                           ('annual leave days', 'annual_leave_days'),
                           ('deduction', 'deduction'),
                           ('annual leave compensation', 'annual_leave_compensation'),
                           ('annual leave charge-back', 'annual_leave_extra_deduction'),
                           ('net payroll', 'net_payroll')):
            if key in row:
                v = row[key]
                w(f'    {label:<28}{v if not isinstance(v, float) else f"{v:,.2f}"}')
        if row.get('attendance_exempt'):
            w(self.style.WARNING(
                f'    NOT attendance-scaled: {row.get("attendance_exempt_reason", "")}'))
        w('')

        # ---- what leave actually exists ---------------------------------
        w(self.style.MIGRATE_LABEL('  LEAVE ON RECORD FOR THIS PERIOD'))
        holidays = set(Holiday.objects.filter(
            date__gte=period.start, date__lte=period.end).values_list('date', flat=True))

        reqs = []
        if kind == 'inhouse':
            reqs = list(LeaveRequest.objects.filter(
                employee=emp, start_date__lte=period.end, end_date__gte=period.start))
        if reqs:
            for lr in reqs:
                days = 0
                d, last = max(lr.start_date, period.start), min(lr.end_date, period.end)
                while d <= last:
                    if d.weekday() != 6 and d not in holidays:
                        days += 1
                    d += datetime.timedelta(days=1)
                w(f'    LeaveRequest  {lr.get_leave_type_display():<16}'
                  f'{lr.start_date} .. {lr.end_date}  '
                  f'{lr.status.upper():<9}{days} working day(s) in period')
        else:
            w('    LeaveRequest  none' + ('' if kind == 'inhouse' else
              '   (remote employees cannot hold one — no remote FK on the model)'))

        field = 'employee' if kind == 'inhouse' else 'remote_employee'
        als = list(AnnualLeave.objects.filter(
            **{field: emp}, start_date__lte=period.end, end_date__gte=period.start))
        if als:
            for al in als:
                w(f'    AnnualLeave   {al.start_date} .. {al.end_date}  '
                  f'{"paid " + str(al.salary_percentage) + "%" if al.is_paid else "UNPAID"}')
        else:
            w('    AnnualLeave   none')

        recs = AttendanceRecord.objects.filter(
            **{field: emp}, date__gte=period.start, date__lte=period.end
        ) if kind == 'inhouse' else []
        w(f'    Attendance    {len(recs) if kind == "inhouse" else "n/a"} record(s) in period')
        w(f'    Holidays      {len(holidays)} in period')
        w('')

        # ---- the verdict -------------------------------------------------
        approved_days = 0
        for lr in reqs:
            if lr.status != 'approved':
                continue
            d, last = max(lr.start_date, period.start), min(lr.end_date, period.end)
            while d <= last:
                if d.weekday() != 6 and d not in holidays:
                    approved_days += 1
                d += datetime.timedelta(days=1)

        daily = float(g('daily_rate'))
        w(self.style.MIGRATE_LABEL('  VERDICT'))
        if approved_days == 0:
            w(self.style.WARNING(
                '    No APPROVED leave request exists for this period. Nothing was'))
            w(self.style.WARNING(
                '    deducted for leave and nothing should be added. If this person did'))
            w(self.style.WARNING(
                '    take leave, it was never recorded as an approved request — that is'))
            w(self.style.WARNING(
                '    a data-entry gap, not a calculation one.'))
        elif float(g('paid_leave_days')) >= approved_days:
            w(self.style.SUCCESS(
                f'    {approved_days} approved leave day(s) were EXCLUDED from absence.'))
            w(self.style.SUCCESS(
                f'    Worth AED {daily * approved_days:,.2f} at {daily:,.2f}/day — money the'))
            w(self.style.SUCCESS(
                '    employee kept because no deduction was raised.'))
            w(self.style.ERROR(
                '    Adding a paid-leave ADDITION on top would pay these days TWICE.'))
        else:
            w(self.style.ERROR(
                f'    {approved_days} approved leave day(s) exist but only'))
            w(self.style.ERROR(
                f'    {g("paid_leave_days")} were credited. The shortfall was deducted:'))
            w(self.style.ERROR(
                f'    AED {daily * (approved_days - float(g("paid_leave_days"))):,.2f}'))
            w(self.style.ERROR(
                '    THIS is the case where paid leave must be added back.'))
        w('')
        w('  Paid-holiday formula for comparison:')
        w(f'    gross {float(emp.salary or 0):,.2f} / {period.days} days'
          f' = {float(emp.salary or 0) / period.days if period.days else 0:,.2f} per day'
          f'  x {approved_days} day(s) = AED '
          f'{(float(emp.salary or 0) / period.days if period.days else 0) * approved_days:,.2f}')
