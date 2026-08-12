"""
Context processors for the attendance app.
These make data available to all templates.
"""

import datetime

from .models import EarlyLeaveRequest
from .views.utils import get_user_nav_sections, has_section_access, MONTH_CHOICES, YEAR_RANGE


def pending_requests_processor(request):
    """
    Add pending on-duty requests count and list to all templates.
    Only for users with access to the On-Duty Requests page.
    """
    if has_section_access(request.user, 'on_duty_requests'):
        pending_requests = EarlyLeaveRequest.objects.filter(
            status='pending'
        ).order_by('-request_date', '-id')
        return {
            'nav_pending_requests': pending_requests,
            'nav_pending_count': pending_requests.count()
        }
    return {
        'nav_pending_requests': [],
        'nav_pending_count': 0
    }


def nav_sections_processor(request):
    """Expose the set of sidebar page keys the current user may access."""
    return {'nav_sections': get_user_nav_sections(request.user)}


def global_period_processor(request):
    """Expose the app-wide working month/year (session-backed) for the sidebar
    period selector, so it stays in sync with whatever month/year the report
    or payroll pages currently have selected."""
    if not request.user.is_authenticated:
        return {}
    now = datetime.datetime.now()
    session = request.session
    return {
        'global_selected_month': session.get('selected_month') or now.month,
        'global_selected_year': session.get('selected_year') or now.year,
        'global_month_choices': MONTH_CHOICES,
        'global_year_choices': YEAR_RANGE,
    }
