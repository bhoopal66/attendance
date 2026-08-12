"""
Leave management views for admin.
Handles leave request viewing, approval, and rejection.
"""

import logging
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required, user_passes_test

from ..models import EarlyLeaveRequest, LeaveRequest
from .utils import section_required

logger = logging.getLogger('attendance')


@login_required
@user_passes_test(section_required('leave_requests'))
def leave_management(request):
    """Admin page to view and manage leave requests."""
    status_filter = request.GET.get('status', 'pending')

    if status_filter == 'all':
        leave_requests = LeaveRequest.objects.all().select_related('employee')
    else:
        leave_requests = LeaveRequest.objects.filter(
            status=status_filter
        ).select_related('employee')

    pending_count = LeaveRequest.objects.filter(status='pending').count()
    approved_count = LeaveRequest.objects.filter(status='approved').count()
    rejected_count = LeaveRequest.objects.filter(status='rejected').count()

    context = {
        'leave_requests': leave_requests,
        'status_filter': status_filter,
        'pending_count': pending_count,
        'approved_count': approved_count,
        'rejected_count': rejected_count,
    }

    return render(request, 'attendance/leave_management.html', context)


@login_required
@user_passes_test(section_required('leave_requests'))
@require_http_methods(["POST"])
def approve_leave(request, leave_id):
    """Approve a leave request with optional day adjustment."""
    try:
        leave_request = LeaveRequest.objects.get(id=leave_id)
    except LeaveRequest.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Leave request not found'}, status=404)

    if leave_request.status != 'pending':
        return JsonResponse({'success': False, 'error': 'This request has already been processed'})

    approved_days = request.POST.get('approved_days')
    start_date_str = request.POST.get('start_date')
    end_date_str = request.POST.get('end_date')
    admin_notes = request.POST.get('admin_notes', '').strip()

    # Update dates if provided
    if start_date_str and end_date_str:
        try:
            from datetime import datetime
            new_start = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            new_end = datetime.strptime(end_date_str, '%Y-%m-%d').date()
            if new_start <= new_end:
                leave_request.start_date = new_start
                leave_request.end_date = new_end
        except ValueError:
            pass  # Keep original dates on invalid format

    try:
        approved_days = int(approved_days) if approved_days else leave_request.requested_days
    except ValueError:
        approved_days = leave_request.requested_days

    current_duration = (leave_request.end_date - leave_request.start_date).days + 1
    approved_days = max(1, min(approved_days, current_duration))

    leave_request.status = 'approved'
    leave_request.approved_days = approved_days
    leave_request.admin_notes = admin_notes
    leave_request.reviewed_at = timezone.now()
    leave_request.save()

    logger.info(
        "Leave approved: id=%s (%d days) by %s",
        leave_id, approved_days, request.user.username
    )

    return JsonResponse({
        'success': True,
        'message': f'Leave approved for {approved_days} day(s)'
    })


@login_required
@user_passes_test(section_required('leave_requests'))
@require_http_methods(["POST"])
def reject_leave(request, leave_id):
    """Reject a leave request."""
    try:
        leave_request = LeaveRequest.objects.get(id=leave_id)
    except LeaveRequest.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Leave request not found'}, status=404)

    if leave_request.status != 'pending':
        return JsonResponse({'success': False, 'error': 'This request has already been processed'})

    admin_notes = request.POST.get('admin_notes', '').strip()

    if not admin_notes:
        return JsonResponse({'success': False, 'error': 'Please provide a reason for rejection'})

    leave_request.status = 'rejected'
    leave_request.admin_notes = admin_notes
    leave_request.reviewed_at = timezone.now()
    leave_request.save()

    logger.info(
        "Leave rejected: id=%s by %s",
        leave_id, request.user.username
    )

    return JsonResponse({'success': True, 'message': 'Leave request rejected'})


@login_required
@user_passes_test(section_required('on_duty_requests'))
def on_duty_requests(request):
    """Admin page to view and manage on-duty (early leave) requests."""
    status_filter = request.GET.get('status', 'pending')

    qs = EarlyLeaveRequest.objects.select_related('employee', 'remote_employee')
    if status_filter != 'all':
        qs = qs.filter(status=status_filter)

    pending_count = EarlyLeaveRequest.objects.filter(status='pending').count()
    approved_count = EarlyLeaveRequest.objects.filter(status='approved').count()
    rejected_count = EarlyLeaveRequest.objects.filter(status='rejected').count()

    context = {
        'on_duty_requests': qs,
        'status_filter': status_filter,
        'pending_count': pending_count,
        'approved_count': approved_count,
        'rejected_count': rejected_count,
    }
    return render(request, 'attendance/on_duty_requests.html', context)
