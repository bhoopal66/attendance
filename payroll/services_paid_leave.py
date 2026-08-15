"""
Paid Leave - showing what the payroll engine has already done, by itself.

WHY THIS MODULE WRITES NOTHING
------------------------------
Employee paid leave is not something that has to be applied. It is applied
the moment it is marked, by `payroll/views.py::_get_inhouse_payroll_row`:

  * An approved `LeaveRequest` day is left out of `absent_days`, so no
    absence deduction is ever raised for it and the day is paid in full.
  * An `AnnualLeave` span with `is_paid=True` adds `salary_percentage`% of
    the daily rate back per working day (`annual_leave_compensation`), and
    charges the remaining (100 - pct)% on the Sundays and holidays inside
    the span (`annual_leave_extra_deduction`), because those days are
    normally paid and on unpaid leave should not be.

Writing a paid-leave *addition* on top of that would pay the same day twice.
So this module is a mirror, not an engine: it reads the rows the payroll
already produced and states, per employee, what leave did to their pay.

The number that matters is `protected` - approved leave days x daily rate -
the deduction that was never raised. It is invisible in the payroll screen
precisely because it is an absence of a charge, and an absence of a charge is
the one thing no report shows you.
"""

import datetime
import logging

from attendance.models import AnnualLeave, LeaveRequest

logger = logging.getLogger('payroll')

WINDOW_PAD = datetime.timedelta(days=40)


def _overlap_days(start, end, p_start, p_end):
    lo, hi = max(start, p_start), min(end, p_end)
    return (hi - lo).days + 1 if hi >= lo else 0


def default_period(year, month):
    """The 21st-20th window, used only to bound the database query."""
    from .services_payroll_engine import get_pay_period

    class _Default:
        salary_cycle_start_day = 21
    return get_pay_period(_Default(), year, month)


def leave_spans(win_start, win_end):
    """Approved leave touching the window, keyed by (employee_type, id).

    Both leave tables are read. `LeaveRequest` is in-house only - the model
    has no remote FK - so a remote employee's leave can only ever arrive as
    an `AnnualLeave` row. That asymmetry is the model's, not this report's,
    and it is surfaced rather than hidden.
    """
    out = {}
    for lr in LeaveRequest.objects.filter(
        status='approved', start_date__lte=win_end, end_date__gte=win_start,
    ).select_related('employee'):
        out.setdefault(('inhouse', lr.employee_id), []).append({
            'kind': 'request',
            'label': lr.get_leave_type_display(),
            'start': lr.start_date, 'end': lr.end_date,
            'paid_pct': 100.0,
            'rejoining': None,
            'note': (lr.reason or '')[:120],
        })

    for al in AnnualLeave.objects.filter(
        start_date__lte=win_end, end_date__gte=win_start,
    ).select_related('employee', 'remote_employee'):
        key = (al.get_employee_type(), al.employee_id or al.remote_employee_id)
        pct = float(al.salary_percentage) if al.is_paid else 0.0
        out.setdefault(key, []).append({
            'kind': 'annual',
            'label': 'Annual leave' + ('' if al.is_paid else ' (unpaid)'),
            'start': al.start_date, 'end': al.end_date,
            'paid_pct': pct,
            'rejoining': al.actual_rejoining_date,
            'note': (al.reason or '')[:120],
        })
    return out


def month_view(year, month):
    """Per-employee leave effect for a month. Computes only - writes nothing."""
    from .services_payroll_engine import (
        SECTION_SALES_PERF_METHOD2, build_all_sections, get_pay_period,
    )

    bound = default_period(year, month)
    spans = leave_spans(bound.start - WINDOW_PAD, bound.end + WINDOW_PAD)

    sections = build_all_sections(year, month)
    seen = set()
    rows = []
    for section, section_rows in sections.items():
        # Method 2 is a comparison view of people already listed under Sales.
        if section == SECTION_SALES_PERF_METHOD2:
            continue
        for row in section_rows:
            emp = row.get('employee')
            if emp is None:
                continue
            emp_type = row.get('employee_type', 'inhouse')
            key = (emp_type, emp.id)
            if key in seen:
                continue
            seen.add(key)

            period = get_pay_period(emp, year, month)
            my_spans = []
            for sp in spans.get(key, []):
                days = _overlap_days(sp['start'], sp['end'], period.start, period.end)
                if days:
                    my_spans.append(dict(sp, days_in_period=days))

            daily = float(row.get('daily_rate') or 0.0)
            approved_days = float(row.get('paid_leave_days') or 0.0)
            annual_days = float(row.get('annual_leave_days') or 0.0)
            comp = float(row.get('annual_leave_compensation') or 0.0)
            extra = float(row.get('annual_leave_extra_deduction') or 0.0)

            # The deduction that was never raised. This is the whole point of
            # the report: it is money the employee kept, and nothing else
            # anywhere in the system shows it.
            protected = round(daily * approved_days, 2)

            if not my_spans and not approved_days and not annual_days and not comp and not extra:
                continue

            rows.append({
                'employee': emp,
                'employee_type': emp_type,
                'section': section,
                'name': emp.name,
                'tcr': getattr(emp, 'tcr_id', '') or '',
                'currency': getattr(emp, 'currency', 'AED') or 'AED',
                'period_start': period.start,
                'period_end': period.end,
                'daily_rate': round(daily, 2),
                'approved_leave_days': approved_days,
                'annual_leave_days': annual_days,
                'protected': protected,
                'compensation': round(comp, 2),
                'extra_deduction': round(extra, 2),
                'net_effect': round(protected + comp - extra, 2),
                'absent_days': float(row.get('absent_days') or 0.0),
                'spans': my_spans,
                'remote_note': ('LeaveRequest has no remote link — only Annual '
                                'Leave can reach this employee'
                                if emp_type == 'remote' else ''),
            })

    rows.sort(key=lambda r: (-r['net_effect'], r['name'].lower()))
    return rows


def totals_by_currency(rows):
    out = {}
    for r in rows:
        cur = out.setdefault(r['currency'], {
            'protected': 0.0, 'compensation': 0.0,
            'extra_deduction': 0.0, 'net_effect': 0.0, 'people': 0,
        })
        cur['protected'] += r['protected']
        cur['compensation'] += r['compensation']
        cur['extra_deduction'] += r['extra_deduction']
        cur['net_effect'] += r['net_effect']
        cur['people'] += 1
    for cur in out.values():
        for k in ('protected', 'compensation', 'extra_deduction', 'net_effect'):
            cur[k] = round(cur[k], 2)
    return out
