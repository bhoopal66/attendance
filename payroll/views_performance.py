"""
Team Performance view — Phase 10.

GET  /payroll/performance/<year>/<month>/   — team performance page
POST                                         — inline target save (JSON)

Kept in a separate file so the payroll/views.py monolith is not touched.
Deploy as: payroll/views_performance.py
"""

import logging
from datetime import date

from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from attendance.views.utils import section_required

logger = logging.getLogger('attendance')

MONTH_NAMES = [
    '', 'January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December',
]


@login_required
@user_passes_test(section_required('payroll'), login_url='/report/')
@require_http_methods(['GET', 'POST'])
def team_performance(request, year, month):
    """
    GET  — render the team performance page for the month.
    POST — save/update one person's monthly target (action=save_target).
    """
    if month < 1 or month > 12:
        raise Http404('Invalid month')

    if request.method == 'POST':
        return _save_target(request, year, month)

    from payroll.services_performance import month_performance

    data = month_performance(year, month)

    # month navigation
    prev_idx = year * 12 + (month - 1) - 1
    next_idx = year * 12 + (month - 1) + 1
    prev_y, prev_m = prev_idx // 12, prev_idx % 12 + 1
    next_y, next_m = next_idx // 12, next_idx % 12 + 1

    context = {
        'year':        year,
        'month':       month,
        'month_name':  MONTH_NAMES[month],
        'rows':        data['rows'],
        'teams':       data['teams'],
        'summary':     data['summary'],
        'prev_year':   prev_y, 'prev_month': prev_m,
        'next_year':   next_y, 'next_month': next_m,
        'today':       date.today(),
    }
    return render(request, 'payroll/performance.html', context)


def _save_target(request, year, month):
    """
    POST action=save_target
        kind             — 'inhouse' | 'remote'
        person_id        — Employee.pk or RemoteEmployee.pk
        target_accounts  — non-negative integer

    Creates or updates the EmployeeTarget row for (person, year, month).
    Returns JSON { ok, target_id, target, achieved, pct, status, status_label }.
    """
    from attendance.models import Employee, RemoteEmployee
    from payroll.models import EmployeeTarget
    from payroll.services_performance import status_for_pct, STATUS_LABELS

    action = request.POST.get('action', '').strip()
    if action != 'save_target':
        return JsonResponse({'ok': False, 'error': 'Unknown action.'}, status=400)

    kind = request.POST.get('kind', '').strip()
    person_id = request.POST.get('person_id', '').strip()
    raw_target = request.POST.get('target_accounts', '').strip()

    if kind not in ('inhouse', 'remote'):
        return JsonResponse({'ok': False, 'error': 'Invalid employee kind.'}, status=400)

    try:
        target_accounts = int(raw_target)
        if target_accounts < 0:
            raise ValueError
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Target must be a non-negative whole number.'}, status=400)

    try:
        if kind == 'inhouse':
            person = Employee.objects.get(pk=person_id)
            fk_kwargs = {'employee': person}
        else:
            person = RemoteEmployee.objects.get(pk=person_id)
            fk_kwargs = {'remote_employee': person}
    except (Employee.DoesNotExist, RemoteEmployee.DoesNotExist, ValueError):
        return JsonResponse({'ok': False, 'error': 'Employee not found.'}, status=404)

    username = request.user.username if request.user.is_authenticated else 'system'

    target, created = EmployeeTarget.objects.update_or_create(
        year=year, month=month, **fk_kwargs,
        defaults={
            'target_accounts': target_accounts,
            'updated_by':      username,
        },
    )
    if created:
        target.created_by = username
        target.save(update_fields=['created_by'])

    achieved = target.achieved_accounts()
    pct = target.achievement_pct()
    status = status_for_pct(pct)

    logger.info(
        'EmployeeTarget %s %s/%s set to %s by %s (%s)',
        target.person_label, year, month, target_accounts, username,
        'created' if created else 'updated',
    )

    return JsonResponse({
        'ok':           True,
        'target_id':    target.pk,
        'target':       target_accounts,
        'achieved':     achieved,
        'pct':          pct,
        'status':       status,
        'status_label': STATUS_LABELS[status],
    })
