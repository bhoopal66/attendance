"""
Management Dashboard view — Phase 12.

URLs:
    /payroll/management/                      → redirect to current month
    /payroll/management/<year>/<month>/       → dashboard  (name='payroll_management')

Access: 'management' section grant (attendance NAV_SECTIONS), same
@user_passes_test(section_required(...)) pattern as the rest of the app.

Kept in a separate file so the payroll/views.py monolith is not touched.
Deploy as: payroll/views_management.py
"""

import logging
from datetime import date

from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from attendance.views.utils import section_required

logger = logging.getLogger('attendance')

MONTH_NAMES = [
    '', 'January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December',
]


@login_required
@user_passes_test(section_required('management'), login_url='/report/')
@require_http_methods(['GET'])
def management_home(request):
    """Redirect /payroll/management/ to the current month."""
    today = date.today()
    return redirect('payroll_management', year=today.year, month=today.month)


@login_required
@user_passes_test(section_required('management'), login_url='/report/')
@require_http_methods(['GET'])
def management_dashboard(request, year, month):
    """Render the executive management dashboard for a month."""
    if month < 1 or month > 12:
        raise Http404('Invalid month')

    from payroll.services_management import management_snapshot

    data = management_snapshot(year, month)

    prev_idx = year * 12 + (month - 1) - 1
    next_idx = year * 12 + (month - 1) + 1

    context = {
        'year':       year,
        'month':      month,
        'month_name': MONTH_NAMES[month],
        'prev_year':  prev_idx // 12, 'prev_month': prev_idx % 12 + 1,
        'next_year':  next_idx // 12, 'next_month': next_idx % 12 + 1,
        'today':      date.today(),
        **data,
    }
    return render(request, 'payroll/management.html', context)
