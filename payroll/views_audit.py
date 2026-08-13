"""
Audit Log view — Phase 13.

GET-only page: /payroll/audit-log/
Name: payroll_audit_log

Access: 'audit_log' section grant (attendance NAV_SECTIONS), same
@user_passes_test(section_required(...)) pattern as the rest of the app.

Kept in a separate file so the payroll/views.py monolith is not touched.
Deploy as: payroll/views_audit.py
"""

import logging

from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from attendance.views.utils import section_required

logger = logging.getLogger('attendance')

PAGE_SIZE = 50


@login_required
@user_passes_test(section_required('audit_log'), login_url='/report/')
@require_http_methods(['GET'])
def audit_log(request):
    """Render the audit trail with optional filters: model, actor, action, date range."""
    from attendance.models import AuditLog

    qs = AuditLog.objects.all()

    model_filter = request.GET.get('model', '').strip()
    actor_filter = request.GET.get('actor', '').strip()
    action_filter = request.GET.get('action', '').strip()
    date_from = request.GET.get('from', '').strip()
    date_to = request.GET.get('to', '').strip()

    if model_filter:
        qs = qs.filter(model_name=model_filter)
    if actor_filter:
        qs = qs.filter(actor__icontains=actor_filter)
    if action_filter:
        qs = qs.filter(action=action_filter)
    if date_from:
        qs = qs.filter(timestamp__date__gte=date_from)
    if date_to:
        qs = qs.filter(timestamp__date__lte=date_to)

    model_choices = list(
        AuditLog.objects.order_by('model_name').values_list('model_name', flat=True).distinct()
    )

    paginator = Paginator(qs, PAGE_SIZE)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    context = {
        'page_obj':      page_obj,
        'model_choices': model_choices,
        'action_choices': AuditLog.ACTION_CHOICES,
        'filters': {
            'model':  model_filter,
            'actor':  actor_filter,
            'action': action_filter,
            'from':   date_from,
            'to':     date_to,
        },
        'total_count': qs.count(),
    }
    return render(request, 'payroll/audit_log.html', context)
