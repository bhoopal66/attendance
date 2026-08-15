"""
Workforce headcount by month — the screen.

Read-only throughout. Nothing here writes, so it is safe to open against a
locked or paid month.
"""

import logging

from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import HttpResponse
from django.shortcuts import render

from attendance.views.utils import MONTH_NAMES, get_selected_month_year, section_required

from . import services_headcount as svc

logger = logging.getLogger('payroll')


def _span(request, year, month):
    """Twelve months ending at the selected month, unless overridden."""
    try:
        months = max(1, min(36, int(request.GET.get('months', 12))))
    except (TypeError, ValueError):
        months = 12
    end_idx = year * 12 + (month - 1)
    start_idx = end_idx - (months - 1)
    return (start_idx // 12, start_idx % 12 + 1, year, month, months)


@login_required
@user_passes_test(section_required('payroll'), login_url='/report/')
def headcount(request):
    month, year = get_selected_month_year(request)
    fy, fm, ty, tm, months = _span(request, year, month)
    rows = svc.range_summary(fy, fm, ty, tm)

    for r in rows:
        r['month_name'] = MONTH_NAMES[r['month']] if r['month'] < len(MONTH_NAMES) else str(r['month'])
        r['label'] = f"{r['month_name'][:3]} {r['year']}"

    if request.GET.get('format') == 'csv':
        import csv
        resp = HttpResponse(content_type='text/csv')
        resp['Content-Disposition'] = (
            f'attachment; filename="headcount_{fy}{fm:02d}_{ty}{tm:02d}.csv"')
        w = csv.writer(resp)
        w.writerow(['Month', 'Period start', 'Period end', 'Employed', 'In-house',
                    'Remote', 'Joiners', 'Leavers', 'Payroll listed', 'Delta',
                    'Paid records', 'Paid but not employed', 'No joining date'])
        for r in rows:
            w.writerow([r['label'], r['period_start'], r['period_end'], r['employed'],
                        r['employed_inhouse'], r['employed_remote'], r['joiners'],
                        r['leavers'], r['payroll_listed'], r['delta'], r['paid_records'],
                        '; '.join(r['paid_but_not_employed']), r['undated']])
        return resp

    latest = rows[-1] if rows else None
    return render(request, 'payroll/headcount.html', {
        'rows': rows, 'months': months, 'year': year, 'month': month,
        'latest': latest,
        'total_joiners': sum(r['joiners'] for r in rows),
        'total_leavers': sum(r['leavers'] for r in rows),
        'any_paid_not_employed': any(r['paid_but_not_employed'] for r in rows),
        'undated_names': latest['undated_names'] if latest else [],
    })
