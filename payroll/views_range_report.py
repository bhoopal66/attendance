"""
Range / Annual Report view — Phase D.

URL: /payroll/range-report/   (GET params: from_year, from_month, to_year, to_month)
Name: payroll_range_report

Aggregates already-paid PaidSalaryRecord snapshots across a From/To month
range, per employee. Deliberately does NOT recompute payroll itself — it
only reports on the locked historical snapshots created when a month is
marked as Paid on the main dashboard, so it can never drift from the
figures already shown/paid elsewhere in the app. A month that has not yet
been marked Paid for a given employee is simply absent from that
employee's total; each row shows "N of M months included" so an
incomplete range is always visible, never silently wrong.

Access: 'payroll' section grant (attendance NAV_SECTIONS), same
@user_passes_test(section_required(...)) pattern as the rest of the app.

Kept in a separate file so the payroll/views.py monolith is not touched.
Deploy as: payroll/views_range_report.py
"""

import json
import logging
from collections import defaultdict
from datetime import date

from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from attendance.views.utils import section_required

from .models import DEDUCTION_CATEGORY_CHOICES, PaidSalaryRecord

logger = logging.getLogger('attendance')

MONTH_NAMES = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
               'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

_DED_COLS = ['advance', 'visa_status_change', 'clawback', 'leave_deduction', 'late_deduction', 'other_deduction']
_ADD_COLS = ['last_month_balance', 'paid_leave', 'other_addition']
_ALL_CATS = _DED_COLS + _ADD_COLS
_CAT_LABELS = dict(DEDUCTION_CATEGORY_CHOICES)


def _parse_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@login_required
@user_passes_test(section_required('payroll'), login_url='/report/')
@require_http_methods(['GET'])
def range_report(request):
    """Render the per-employee Range / Annual report for a From/To month window."""
    today = date.today()
    from_year = _parse_int(request.GET.get('from_year'), today.year)
    from_month = min(max(_parse_int(request.GET.get('from_month'), 1), 1), 12)
    to_year = _parse_int(request.GET.get('to_year'), today.year)
    to_month = min(max(_parse_int(request.GET.get('to_month'), 12), 1), 12)

    from_idx = from_year * 12 + (from_month - 1)
    to_idx = to_year * 12 + (to_month - 1)
    if from_idx > to_idx:
        from_idx, to_idx = to_idx, from_idx
        from_year, from_month, to_year, to_month = to_year, to_month, from_year, from_month

    total_months_in_range = to_idx - from_idx + 1

    # Narrow with a coarse year filter first (fast, indexed), then apply the
    # exact month-precision range check in Python — year/month are separate
    # integer fields, so there's no single indexed range condition for this.
    candidates = PaidSalaryRecord.objects.filter(
        year__gte=from_year, year__lte=to_year,
    ).select_related('employee', 'remote_employee')
    records = [r for r in candidates if from_idx <= (r.year * 12 + (r.month - 1)) <= to_idx]

    by_emp = {}
    for r in records:
        emp = r.employee or r.remote_employee
        if emp is None:
            continue
        emp_type = 'inhouse' if r.employee_id else 'remote'
        key = (emp_type, emp.id)
        snap = r.snapshot or {}

        agg = by_emp.get(key)
        if agg is None:
            agg = {
                'employee_name': emp.name,
                'employee_type': emp_type,
                'department': snap.get('department') or getattr(emp, 'department', '') or '',
                'currency': r.currency,
                'currency_mismatch': False,
                'months_included': 0,
                'net_payroll': 0.0,
                'total_deductions': 0.0,
                'total_additions': 0.0,
                'final_salary': 0.0,
                'categories': {c: 0.0 for c in _ALL_CATS},
            }
            by_emp[key] = agg

        if agg['currency'] != r.currency:
            agg['currency_mismatch'] = True

        agg['months_included'] += 1
        agg['net_payroll'] = round(agg['net_payroll'] + float(snap.get('net_payroll', 0) or 0), 2)
        agg['total_deductions'] = round(agg['total_deductions'] + float(snap.get('total_deductions', 0) or 0), 2)
        agg['total_additions'] = round(agg['total_additions'] + float(snap.get('total_additions', 0) or 0), 2)
        agg['final_salary'] = round(agg['final_salary'] + float(r.final_salary or 0), 2)
        cat = snap.get('deductions_breakdown') or {}
        for c in _ALL_CATS:
            agg['categories'][c] = round(agg['categories'][c] + float(cat.get(c, 0) or 0), 2)

    rows = sorted(by_emp.values(), key=lambda x: x['employee_name'].lower())
    for row in rows:
        row['months_total'] = total_months_in_range
        row['is_complete'] = row['months_included'] >= total_months_in_range
        nonzero_categories = {c: v for c, v in row['categories'].items() if v}
        # Pre-serialized for the client-side breakdown modal — do not render
        # the Python dict directly in the template, it is not valid JS/JSON.
        row['categories_json'] = json.dumps(nonzero_categories)

    totals_by_currency = defaultdict(lambda: {
        'net_payroll': 0.0, 'total_deductions': 0.0, 'total_additions': 0.0,
        'final_salary': 0.0, 'employees': 0,
    })
    for row in rows:
        t = totals_by_currency[row['currency']]
        t['net_payroll'] = round(t['net_payroll'] + row['net_payroll'], 2)
        t['total_deductions'] = round(t['total_deductions'] + row['total_deductions'], 2)
        t['total_additions'] = round(t['total_additions'] + row['total_additions'], 2)
        t['final_salary'] = round(t['final_salary'] + row['final_salary'], 2)
        t['employees'] += 1

    context = {
        'from_year': from_year, 'from_month': from_month,
        'to_year': to_year, 'to_month': to_month,
        'from_month_name': MONTH_NAMES[from_month],
        'to_month_name': MONTH_NAMES[to_month],
        'total_months_in_range': total_months_in_range,
        'rows': rows,
        'totals_by_currency': dict(totals_by_currency),
        'cat_labels_json': json.dumps(_CAT_LABELS),
        'month_choices': list(enumerate(MONTH_NAMES))[1:],  # [(1, 'Jan'), (2, 'Feb'), ...]
        'year_choices': list(range(today.year - 3, today.year + 2)),
        'today_year': today.year,
    }
    return render(request, 'payroll/range_report.html', context)
