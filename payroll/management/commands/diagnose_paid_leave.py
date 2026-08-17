"""Why was this employee's salary reduced, and was leave honoured?

READ-ONLY. Opens no transaction, writes nothing, creates nothing.

The question this answers is not "is paid leave calculated" but WHICH of three
different things happened, because they need opposite fixes:

  A. days were charged as ABSENT even though they were marked paid leave, or
     covered by an APPROVED leave request
       -> the marking is being ignored. An engine bug. Fix the counting.
  B. days were charged as absent and were never marked as anything
       -> the engine is right. The marking was never made.
  C. the marked days were NOT charged, and the only thing missing is that the
     payslip does not SAY "paid leave" anywhere
       -> a display gap. Adding money would pay the same day twice.

    manage.py diagnose_paid_leave --tcr TCR1000224 --year 2026 --month 1
"""
import calendar
import datetime

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Explain, day by day, why one employee's salary was reduced."

    def add_arguments(self, parser):
        parser.add_argument('--tcr', required=True, help='Employee tcr_id, e.g. TCR1000224')
        parser.add_argument('--year', type=int, required=True)
        parser.add_argument('--month', type=int, required=True)

    def handle(self, *args, **opts):
        TCR = opts['tcr']
        YEAR = opts['year']
        MONTH = opts['month']
        run(TCR, YEAR, MONTH)


def run(TCR, YEAR, MONTH):
    from attendance.models import (
        AnnualLeave, AttendanceRecord, Employee, Holiday, LeaveRequest,
        MonthlySummary,
    )
    from payroll.models import DeductionEntry

    D = datetime.timedelta


    def money(v):
        try:
            return f'{float(v):,.2f}'
        except (TypeError, ValueError):
            return str(v)


    def line(ch='-'):
        print(ch * 78)


    emp = Employee.objects.filter(tcr_id=TCR).first()
    if emp is None:
        emp = Employee.objects.filter(name__icontains=TCR).first()
    if emp is None:
        print(f'NO in-house employee with tcr_id {TCR!r}.')
        cand = Employee.objects.filter(is_active=True).order_by('name')[:400]
        print('Active in-house employees:')
        for e in cand:
            print(f'   {e.tcr_id or "-":<14} {e.name}')
        return

    line('=')
    print(f'{emp.name}   tcr={emp.tcr_id}   id={emp.id}')
    line('=')
    print(f'  department        {getattr(emp, "department", None)}')
    print(f'  payroll_type      {getattr(emp, "payroll_type", "attendance")}')
    print(f'  is_fixed_salary   {getattr(emp, "is_fixed_salary", None)}')
    print(f'  salary            {money(emp.salary)} {emp.currency}')
    print(f'  joining_date      {emp.joining_date}')
    print(f'  cycle_start_day   {getattr(emp, "salary_cycle_start_day", None)}')
    print(f'  is_active         {emp.is_active}')

    # ------------------------------------------------------------- the pay period
    try:
        from payroll.services_payroll_engine import get_pay_period
        p = get_pay_period(emp, YEAR, MONTH)
        p_start, p_end, p_days = p.start, p.end, p.days
        p_hol = getattr(p, 'holidays', None)
    except Exception as exc:                                    # noqa: BLE001
        print(f'\n  (engine period lookup failed: {exc!r} — falling back to calendar month)')
        p_start = datetime.date(YEAR, MONTH, 1)
        p_end = datetime.date(YEAR, MONTH, calendar.monthrange(YEAR, MONTH)[1])
        p_days = (p_end - p_start).days + 1
        p_hol = None

    print()
    print(f'PAY PERIOD {YEAR}-{MONTH:02d}:  {p_start}  ..  {p_end}   ({p_days} days)')
    print(f'  holidays counted by the engine for this period: {p_hol}')
    if emp.salary:
        print(f'  daily rate = {money(emp.salary)} / {p_days} = {money(float(emp.salary) / p_days)}')
        print(f'  one day     = {money(float(emp.salary) / p_days)}')
        print(f'  two days    = {money(2 * float(emp.salary) / p_days)}')

    # ---------------------------------------------------------------- engine row
    row = None
    line()
    print('ENGINE OUTPUT (what the payroll page shows)')
    line()
    try:
        from payroll.services_payroll_engine import calculate_employee_payroll
        row = calculate_employee_payroll(emp, 'inhouse', YEAR, MONTH)
    except Exception as exc:                                    # noqa: BLE001
        print(f'  could not build the row: {type(exc).__name__}: {exc}')

    if row:
        keys = [
            'salary', 'gross_salary', 'daily_rate', 'total_working_days',
            'present_days', 'full_days', 'half_days', 'late_days',
            'absent_days', 'total_deduction_days', 'paid_leave_days',
            'approved_leave_days', 'annual_leave_days',
            'annual_leave_compensation', 'annual_leave_extra_deduction',
            'bridge_sunday_count', 'deduction', 'late_deduction',
            'base_salary', 'incentives', 'reductions',
            'total_additions', 'total_deductions', 'net_payroll',
            'attendance_exempt', 'attendance_exempt_reason',
        ]
        for k in keys:
            if k in row:
                print(f'  {k:<32} {money(row[k]) if isinstance(row[k], (int, float)) else row[k]}')
        missing = [k for k in keys if k not in row]
        if missing:
            print(f'  (row has no key: {", ".join(missing)})')

    # ------------------------------------------------------------ raw attendance
    hol_dates = set(Holiday.objects.filter(
        date__gte=p_start, date__lte=p_end).values_list('date', flat=True))
    hol_names = {h.date: h.name for h in Holiday.objects.filter(
        date__gte=p_start, date__lte=p_end)}

    recs = {r.date: r for r in AttendanceRecord.objects.filter(
        employee=emp, date__gte=p_start, date__lte=p_end)}

    # Sundays the bridge rule charges. get_bridge_sunday_days returns DAY
    # NUMBERS, which is only unambiguous because a 21st-20th period never
    # repeats a day number; do not reuse this shortcut on a longer window.
    bridge_days = set()
    try:
        from attendance.views.utils import get_bridge_sunday_days
        bridge_days = set(get_bridge_sunday_days(emp, p_start, p_end))
    except Exception as exc:                                    # noqa: BLE001
        print(f'  (bridge-Sunday lookup failed: {exc!r})')

    approved = []
    for lr in LeaveRequest.objects.filter(
            employee=emp, start_date__lte=p_end, end_date__gte=p_start):
        approved.append(lr)

    approved_dates = set()
    for lr in approved:
        if lr.status != 'approved':
            continue
        c, e = max(lr.start_date, p_start), min(lr.end_date, p_end)
        while c <= e:
            approved_dates.add(c)
            c += D(days=1)

    annual = list(AnnualLeave.objects.filter(
        employee=emp, start_date__lte=p_end, end_date__gte=p_start))
    annual_dates = set()
    for al in annual:
        c, e = max(al.start_date, p_start), min(al.end_date, p_end)
        while c <= e:
            annual_dates.add(c)
            c += D(days=1)

    line()
    print('DAY BY DAY  (what is actually on record)')
    line()
    print('  date        day  holiday       record  flags              reads as')
    charged, marked_pl, marked_wfh, punched = [], [], [], []
    c = p_start
    while c <= p_end:
        r = recs.get(c)
        is_sun = c.weekday() == 6
        is_hol = c in hol_dates
        flags = []
        if r:
            if r.first_in:
                flags.append('punch ' + r.first_in.strftime('%H:%M'))
            if getattr(r, 'is_work_from_home', False):
                flags.append('WFH')
            if getattr(r, 'is_paid_leave', False):
                flags.append('PAID-LEAVE')
        if c in approved_dates:
            flags.append('approved-leave')
        if c in annual_dates:
            flags.append('annual-leave')

        if is_sun:
            reads = ('BRIDGE SUNDAY - CHARGED (leave either side)'
                     if c.day in bridge_days else 'Sunday')
        elif is_hol:
            reads = 'holiday'
        elif r and (r.first_in or getattr(r, 'is_work_from_home', False)):
            reads = 'PRESENT'
            punched.append(c)
        elif r and getattr(r, 'is_paid_leave', False):
            reads = 'paid leave — not deducted'
            marked_pl.append(c)
        elif c in approved_dates:
            reads = 'approved leave — not deducted'
        elif c in annual_dates:
            reads = 'annual leave'
        else:
            reads = '*** CHARGED AS ABSENT ***'
            charged.append(c)

        if r and getattr(r, 'is_work_from_home', False):
            marked_wfh.append(c)

        print(f'  {c}  {c.strftime("%a")}  {(hol_names.get(c) or "")[:12]:<12}  '
              f'{"yes" if r else "-":<6}  {",".join(flags)[:18]:<18} {reads}')
        c += D(days=1)

    line()
    print('COUNTS OVER THE PERIOD')
    line()
    print(f'  attendance records present            {len(recs)}')
    print(f'  days with a punch or WFH              {len(punched)}')
    print(f'  days flagged is_paid_leave            {len(marked_pl)}   {[str(d) for d in marked_pl]}')
    print(f'  days flagged work-from-home           {len(marked_wfh)}')
    print(f'  public holidays in period             {len(hol_dates)}   {[str(d) for d in sorted(hol_dates)]}')
    print(f'  dates inside an APPROVED LeaveRequest {len(approved_dates)}')
    print(f'  dates inside an AnnualLeave span      {len(annual_dates)}')
    print(f'  days this script reads as absent      {len(charged)}   {[str(d) for d in charged]}')

    line()
    print('LEAVE ON RECORD')
    line()
    if not approved:
        print('  NO LeaveRequest of any status touches this period.')
    for lr in approved:
        print(f'  LeaveRequest #{lr.id}  {lr.leave_type:<10} {lr.start_date} .. {lr.end_date}  '
              f'status={lr.status}  requested={getattr(lr, "requested_days", None)}  '
              f'approved={getattr(lr, "approved_days", None)}')
    if not annual:
        print('  NO AnnualLeave row touches this period.')
    for al in annual:
        print(f'  AnnualLeave  #{al.id}  {al.start_date} .. {al.end_date}  '
              f'is_paid={al.is_paid}  pct={al.salary_percentage}  '
              f'rejoin={getattr(al, "actual_rejoining_date", None)}')

    line()
    print('DEDUCTION / ADDITION ENTRIES BOOKED FOR THIS MONTH')
    line()
    des = DeductionEntry.objects.filter(employee=emp)
    hit = 0
    for de in des:
        span = de.split_months or 1
        idx = (YEAR - de.start_year) * 12 + (MONTH - de.start_month)
        if 0 <= idx < span:
            hit += 1
            print(f'  #{de.id}  {de.category:<20} {money(de.total_amount)} {de.currency}  '
                  f'split {span}  type={de.entry_type}  note={(de.note or "")[:40]}')
    if not hit:
        print('  none.')

    ms = MonthlySummary.objects.filter(employee=emp, year=YEAR, month=MONTH).first()
    line()
    print('MONTHLY SUMMARY (calendar month — not the pay period)')
    line()
    if ms:
        print(f'  working_days={ms.working_days}  leave_days={ms.leave_days}  '
              f'late_days={ms.late_days}  half_days={ms.half_days}')
        print('  NOTE: the pay period is 21st-20th, so for a cross-month period the')
        print('        engine reads AttendanceRecord directly and this row is NOT used.')
    else:
        print('  no summary row for this calendar month.')

    # -------------------------------------------------------------------- verdict
    line('=')
    print('VERDICT')
    line('=')
    eng_absent = None
    if row:
        for k in ('total_deduction_days', 'absent_days'):
            if k in row:
                eng_absent = float(row[k])
                break

    if marked_pl and charged and set(marked_pl) & set(charged):
        print('  CASE A — days marked PAID LEAVE were still charged as absent.')
        print('  The marking is being ignored. This is an engine bug: fix the')
        print('  counting, do NOT add money on top.')
    elif marked_pl and not (set(marked_pl) & set(charged)):
        print('  CASE C — the paid-leave days were NOT charged. Nothing was lost.')
        print(f'  Days honoured: {[str(d) for d in marked_pl]}')
        if charged:
            print(f'  The {len(charged)} day(s) that WERE charged are unmarked absences:')
            print(f'    {[str(d) for d in charged]}')
            print('  Those are a different thing from paid leave. Marking them paid')
            print('  leave (or approving leave for them) is what removes the charge.')
        print('  What is missing is only that the payslip never SAYS "paid leave".')
        print('  Adding a paid-leave ADDITION here would pay those days twice.')
    elif charged and not marked_pl and not approved_dates:
        print('  CASE B — days were charged as absent and nothing on record excuses')
        print('  them: no paid-leave flag, no approved leave request, no annual leave.')
        print(f'  Charged: {[str(d) for d in charged]}')
        print('  The engine is arithmetically right. The marking was never made,')
        print('  or was made in a different month than the pay period.')
    elif not charged:
        print('  Nothing was charged as absent over this period.')
        if eng_absent:
            print(f'  But the engine reports {eng_absent} deduction day(s) — so the')
            print('  reduction is coming from somewhere else: late deduction, the')
            print('  Sunday bridge, or annual-leave charge-back. See the row above.')
    else:
        print('  Mixed picture — read the day-by-day table above; it is the evidence.')

    if eng_absent is not None and len(charged) != eng_absent:
        gap = eng_absent - len(charged)
        n_bridge = len(bridge_days)
        print()
        if gap == n_bridge and n_bridge:
            print(f'  The engine charges {eng_absent} day(s) where the calendar above shows')
            print(f'  {len(charged)}. The difference is {n_bridge} BRIDGE SUNDAY(S):')
            print(f'    {sorted(bridge_days)}  (day numbers in this period)')
            print('  A Sunday with approved leave on the day before AND the day after')
            print('  is charged one day, deliberately, "regardless of whether the')
            print('  surrounding leaves are themselves paid or unpaid"')
            print('  (attendance/views/utils.py, get_bridge_sunday_days).')
            print('  That is a POLICY, not a miscalculation. If leave either side of a')
            print('  Sunday should no longer cost that Sunday, say so and it changes')
            print('  in one place — but it is a rule change, not a bug fix.')
        else:
            print(f'  DISAGREEMENT: this script counts {len(charged)} absent day(s), the')
            print(f'  engine reports {eng_absent}, and {n_bridge} bridge Sunday(s) does')
            print('  not account for the gap. Something else is charging days: a late')
            print('  deduction, an annual-leave charge-back, or a stale summary row.')
            print('  Do not act on either figure until this is explained.')

    print()
    print('  The paid-holiday formula, for comparison:')
    if emp.salary:
        per = float(emp.salary) / p_days
        print(f'    gross / period days x days  =  {money(emp.salary)} / {p_days} '
              f'x N  =  {money(per)} x N')
    print()
    print('READ-ONLY: this script wrote nothing.')

