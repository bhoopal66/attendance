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


def _shift_month(year, month, delta):
    """Return (year, month) shifted by `delta` months, wrapping across years."""
    idx = year * 12 + (month - 1) + delta
    y, m = divmod(idx, 12)
    return y, m + 1


def _build_presets(today):
    """Phase E7 quick-range presets.

    The 'last N months' windows are inclusive of the current month, and the
    arithmetic wraps years — in February, "last 3 months" correctly reaches
    back to December of the previous year rather than clamping at January.
    """
    presets = []
    for label, back in (('Last 3 months', 2), ('Last 6 months', 5)):
        fy, fm = _shift_month(today.year, today.month, -back)
        presets.append({
            'label': label,
            'query': (f'mode=range&from_month={fm}&from_year={fy}'
                      f'&to_month={today.month}&to_year={today.year}'),
        })
    presets.append({
        'label': 'This year',
        'query': f'mode=annual&annual_year={today.year}',
    })
    presets.append({
        'label': 'Since joining',
        'query': 'mode=since_joining',
        'note': 'per employee',
    })
    return presets


@login_required
@user_passes_test(section_required('payroll'), login_url='/report/')
@require_http_methods(['GET'])
def range_report(request):
    """Render the per-employee Range / Annual report.

    Two selection modes, both aggregating the same locked PaidSalaryRecord
    snapshots — only the record filter differs:
      - 'range' (default): a contiguous From/To month window.
      - 'multi' (Phase E3): an arbitrary, non-contiguous set of months
        within a single year (e.g. Jan + Mar + Aug 2026).
    """
    today = date.today()
    mode = request.GET.get('mode') or 'range'

    from_year = _parse_int(request.GET.get('from_year'), today.year)
    from_month = min(max(_parse_int(request.GET.get('from_month'), 1), 1), 12)
    to_year = _parse_int(request.GET.get('to_year'), today.year)
    to_month = min(max(_parse_int(request.GET.get('to_month'), 12), 1), 12)

    multi_year = _parse_int(request.GET.get('multi_year'), today.year)
    _months_raw = request.GET.get('months', '')
    selected_months = sorted({
        m for m in (_parse_int(tok, 0) for tok in _months_raw.split(',') if tok.strip())
        if 1 <= m <= 12
    })

    annual_year = _parse_int(request.GET.get('annual_year'), today.year)
    today_idx = today.year * 12 + (today.month - 1)
    annual_end_month = today.month if annual_year == today.year else 12

    if mode == 'annual':
        # ---- Phase E7: Annual (year to date) ----
        # A completed year runs Jan–Dec; the current year stops at the current
        # month, so an employee never reads as "3 of 12 months" in August purely
        # because the rest of the year has not happened yet.
        total_months_in_range = annual_end_month
        candidates = PaidSalaryRecord.objects.filter(
            year=annual_year, month__lte=annual_end_month,
        ).select_related('employee', 'remote_employee')
        records = list(candidates)
        period_label = (
            f'{MONTH_NAMES[1]} – {MONTH_NAMES[annual_end_month]} {annual_year}'
            + (' (year to date)' if annual_year == today.year else '')
        )
    elif mode == 'since_joining':
        # ---- Phase E7: Since joining ----
        # Every month ever paid, up to the current one. The window is entirely
        # per-employee here, so total_months_in_range is only a display fallback
        # — the real denominator is computed per row below.
        candidates = PaidSalaryRecord.objects.filter(
            year__lte=today.year,
        ).select_related('employee', 'remote_employee')
        records = [r for r in candidates if (r.year * 12 + (r.month - 1)) <= today_idx]
        total_months_in_range = 0
        period_label = 'Since each employee joined'
    elif mode == 'multi' and selected_months:
        # ---- Phase E3: Multiple Months — arbitrary months within one year ----
        total_months_in_range = len(selected_months)
        candidates = PaidSalaryRecord.objects.filter(
            year=multi_year, month__in=selected_months,
        ).select_related('employee', 'remote_employee')
        records = list(candidates)
        period_label = ', '.join(MONTH_NAMES[m] for m in selected_months) + f' {multi_year}'
    else:
        mode = 'range'  # normalize (also covers 'multi' requested with no months picked yet)
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
        period_label = f'{MONTH_NAMES[from_month]} {from_year} – {MONTH_NAMES[to_month]} {to_year}'

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
                'joining_date': getattr(emp, 'joining_date', None),
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
        # ---- Phase E7: per-employee denominator ----
        # In the fixed-window modes every employee is measured against the same
        # span. In Annual and Since-joining the span starts at the employee's
        # own joining date, so someone who joined in June is not reported as
        # "3 of 8 months" — they were only ever owed 3.
        _join = row.get('joining_date')
        if mode == 'annual':
            if _join and _join.year == annual_year:
                _start_month = min(max(_join.month, 1), annual_end_month)
            elif _join and _join.year > annual_year:
                _start_month = annual_end_month + 1  # joined after this year → nothing owed
            else:
                _start_month = 1
            row['months_total'] = max(0, annual_end_month - _start_month + 1)
            row['window_note'] = (
                f'from {MONTH_NAMES[_start_month]} (joined {_join:%d %b %Y})'
                if _join and _join.year == annual_year and _start_month > 1 else ''
            )
        elif mode == 'since_joining':
            if _join:
                _join_idx = _join.year * 12 + (_join.month - 1)
                row['months_total'] = max(0, today_idx - _join_idx + 1)
                row['window_note'] = f'joined {_join:%d %b %Y}'
            else:
                # No joining date recorded — the denominator is unknowable, so
                # report what was found rather than inventing a target.
                row['months_total'] = row['months_included']
                row['window_note'] = 'no joining date on file'
        else:
            row['months_total'] = total_months_in_range
            row['window_note'] = ''
        row['is_complete'] = row['months_included'] >= row['months_total']
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
        'mode': mode,
        'from_year': from_year, 'from_month': from_month,
        'to_year': to_year, 'to_month': to_month,
        'from_month_name': MONTH_NAMES[from_month],
        'to_month_name': MONTH_NAMES[to_month],
        'multi_year': multi_year,
        'selected_months': selected_months,
        'annual_year': annual_year,
        # Phase E7 presets — computed here rather than in the template so the
        # month arithmetic wraps across year boundaries correctly (a "last 3
        # months" link in February must reach back into the previous year).
        'presets': _build_presets(today),
        'period_label': period_label,
        'total_months_in_range': total_months_in_range,
        'rows': rows,
        'totals_by_currency': dict(totals_by_currency),
        'cat_labels_json': json.dumps(_CAT_LABELS),
        'month_choices': list(enumerate(MONTH_NAMES))[1:],  # [(1, 'Jan'), (2, 'Feb'), ...]
        'year_choices': list(range(today.year - 3, today.year + 2)),
        'today_year': today.year,
    }
    return render(request, 'payroll/range_report.html', context)
