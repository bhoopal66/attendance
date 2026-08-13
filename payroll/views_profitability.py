"""
Profitability view — Phase 11.

GET-only page:  /payroll/profitability/<year>/<month>/
Name:           payroll_profitability

Kept in a separate file so the payroll/views.py monolith is not touched.
Deploy as: payroll/views_profitability.py
"""

import logging
from datetime import date

from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

logger = logging.getLogger('attendance')

MONTH_NAMES = [
    '', 'January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December',
]


@login_required
@require_http_methods(['GET'])
def profitability(request, year, month):
    """Render the profitability page for the month."""
    if month < 1 or month > 12:
        raise Http404('Invalid month')

    from payroll.services_profitability import month_profitability

    data = month_profitability(year, month)

    # month navigation
    prev_idx = year * 12 + (month - 1) - 1
    next_idx = year * 12 + (month - 1) + 1

    context = {
        'year':        year,
        'month':       month,
        'month_name':  MONTH_NAMES[month],
        'rows':        data['rows'],
        'teams':       data['teams'],
        'summary':     data['summary'],
        'fx_missing':  data['fx_missing'],
        'prev_year':   prev_idx // 12, 'prev_month': prev_idx % 12 + 1,
        'next_year':   next_idx // 12, 'next_month': next_idx % 12 + 1,
        'today':       date.today(),
    }
    return render(request, 'payroll/profitability.html', context)
