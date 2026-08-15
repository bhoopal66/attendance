"""
Workforce headcount by month.

WHY THIS EXISTS
---------------
The payroll dashboard builds its roster from `is_active=True` - who is employed
*today* - not from who was employed during the period being viewed. That is why
every month shows the same number of rows regardless of which month you pick.

For a live month that is fine. For a historical one it is not: someone who
joined in June appears in January's payroll, and someone who left in March
appears in April's. This module answers the other question - who was actually
employed in each month, according to `joining_date` and `leaving_date` - and
puts the two numbers side by side so the gap is visible rather than implied.

The column worth reading is `paid_but_not_employed`. A non-zero value in a month
that has been paid means payroll issued money to someone outside their
employment dates. That is a payroll error, not a reporting quirk.

PERIOD
------
Headcount is measured over the month's DEFAULT pay period (21st of the previous
month to the 20th), the same span the dashboard header prints. Employees can
carry their own `salary_cycle_start_day`, so a handful sit on slightly
different periods; the difference only matters for someone joining or leaving
within a few days of the boundary, and the per-employee dates are listed in the
detail rows so those cases are checkable rather than hidden.

DATA QUALITY
------------
An employee with no `joining_date` cannot be placed in time at all. They are
counted separately as `undated` and never silently assumed present - assuming
would inflate every month by the same unknown number.
"""

import calendar
import datetime
import logging

logger = logging.getLogger('payroll')


def default_period(year, month):
    """The 21st-to-20th span the dashboard prints for a month."""
    if month == 1:
        prev_y, prev_m = year - 1, 12
    else:
        prev_y, prev_m = year, month - 1
    start = datetime.date(prev_y, prev_m, min(21, calendar.monthrange(prev_y, prev_m)[1]))
    end = datetime.date(year, month, min(20, calendar.monthrange(year, month)[1]))
    return start, end


def _employed_during(emp, start, end):
    """Was this person employed at any point inside the period?"""
    if not emp.joining_date:
        return False
    if emp.joining_date > end:
        return False
    if emp.leaving_date and emp.leaving_date < start:
        return False
    return True


def payroll_roster_ids():
    """Exactly who the payroll dashboard lists — today's active staff.

    Mirrors `payroll.services_payroll_engine.select_employees`, including the
    tcr_id de-duplication, so the comparison is against what payroll really
    shows rather than an approximation of it.
    """
    from attendance.models import Employee, RemoteEmployee

    inhouse = list(Employee.objects.filter(is_active=True,
                                           department__in=['Admin', 'Sales']))
    tcr = {(e.tcr_id or '').strip() for e in inhouse if (e.tcr_id or '').strip()}
    remote_qs = RemoteEmployee.objects.filter(is_active=True)
    if tcr:
        remote_qs = remote_qs.exclude(tcr_id__in=tcr)
    return ({('inhouse', e.id) for e in inhouse}
            | {('remote', e.id) for e in remote_qs})


def month_summary(year, month, roster=None):
    """Headcount facts for one month."""
    from attendance.models import Employee, RemoteEmployee
    from .models import PaidSalaryRecord

    start, end = default_period(year, month)
    roster = payroll_roster_ids() if roster is None else roster

    employed, joiners, leavers, undated = [], [], [], []
    for model, kind in ((Employee, 'inhouse'), (RemoteEmployee, 'remote')):
        for emp in model.objects.all():
            if not emp.joining_date:
                undated.append((kind, emp))
                continue
            if _employed_during(emp, start, end):
                employed.append((kind, emp))
            if start <= emp.joining_date <= end:
                joiners.append((kind, emp))
            if emp.leaving_date and start <= emp.leaving_date <= end:
                leavers.append((kind, emp))

    employed_ids = {(k, e.id) for k, e in employed}

    paid_ids = set()
    for r in PaidSalaryRecord.objects.filter(year=year, month=month):
        paid_ids.add(('inhouse', r.employee_id) if r.employee_id
                     else ('remote', r.remote_employee_id))

    # The two findings this report exists to surface.
    paid_not_employed = paid_ids - employed_ids
    listed_not_employed = roster - employed_ids
    employed_not_listed = employed_ids - roster

    def _name(key):
        kind, pk = key
        model = Employee if kind == 'inhouse' else RemoteEmployee
        obj = model.objects.filter(id=pk).first()
        return f'{obj.name} ({kind})' if obj else f'#{pk} ({kind})'

    return {
        'year': year, 'month': month,
        'period_start': start, 'period_end': end,
        'employed': len(employed),
        'employed_inhouse': sum(1 for k, _ in employed if k == 'inhouse'),
        'employed_remote': sum(1 for k, _ in employed if k == 'remote'),
        'joiners': len(joiners),
        'joiner_names': sorted(e.name for _k, e in joiners),
        'leavers': len(leavers),
        'leaver_names': sorted(e.name for _k, e in leavers),
        'undated': len(undated),
        'undated_names': sorted(e.name for _k, e in undated),
        'payroll_listed': len(roster),
        'delta': len(roster) - len(employed),
        'paid_records': len(paid_ids),
        'paid_but_not_employed': sorted(_name(k) for k in paid_not_employed),
        'listed_but_not_employed': sorted(_name(k) for k in listed_not_employed),
        'employed_but_not_listed': sorted(_name(k) for k in employed_not_listed),
    }


def range_summary(from_year, from_month, to_year, to_month):
    """One row per month across a span. Roster is read once, not per month."""
    roster = payroll_roster_ids()
    out = []
    y, m = from_year, from_month
    guard = 0
    while (y, m) <= (to_year, to_month) and guard < 120:
        out.append(month_summary(y, m, roster=roster))
        m += 1
        if m > 12:
            y, m = y + 1, 1
        guard += 1
    return out
