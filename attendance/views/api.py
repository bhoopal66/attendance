"""
API endpoints for attendance management.
Handles attendance updates, request approval, and related API calls.
"""

import json
import datetime
import calendar
import logging
from datetime import timedelta

from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.contrib.auth.decorators import login_required, user_passes_test

from ..models import (
    AnnualLeave, AttendanceRecord, EarlyLeaveRequest, Employee, MonthlySummary,
    RemoteCallRecord, RemoteEmployee,
)
from .utils import (
    get_employee_shift_for_date, get_saturday_shift, superuser_required,
    has_section_access, section_required,
)

logger = logging.getLogger('attendance')


@login_required
def set_period(request):
    """Set the app-wide working month/year (stored in session) and bounce back
    to the referring page with month/year applied to its query string, so the
    same period follows the user across the report, payroll, etc."""
    next_url = request.GET.get('next', '')
    if not next_url or not url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        next_url = reverse('report')

    try:
        month = int(request.GET.get('month'))
        year = int(request.GET.get('year'))
        if 1 <= month <= 12 and 2000 <= year <= 2099:
            request.session['selected_month'] = month
            request.session['selected_year'] = year
    except (TypeError, ValueError):
        month = request.session.get('selected_month')
        year = request.session.get('selected_year')

    if month and year:
        from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
        parts = urlsplit(next_url)
        query = dict(parse_qsl(parts.query))
        query['month'] = month
        query['year'] = year
        next_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

    return redirect(next_url)


@login_required
def get_pending_count(request):
    """API endpoint to get current pending request count for real-time updates."""
    if not has_section_access(request.user, 'on_duty_requests'):
        return JsonResponse({'count': 0})
    count = EarlyLeaveRequest.objects.filter(status='pending').count()
    return JsonResponse({'count': count})


@login_required
def get_pending_requests(request):
    """API endpoint to get all pending requests with full details for real-time dropdown."""
    if not has_section_access(request.user, 'on_duty_requests'):
        return JsonResponse({'requests': [], 'count': 0})

    pending = EarlyLeaveRequest.objects.filter(
        status='pending'
    ).select_related(
        'employee', 'remote_employee'
    ).order_by('-request_date')[:10]

    requests_data = [{
        'id': req.id,
        'employee_name': req.get_employee_name(),
        'request_date': req.request_date.strftime('%Y-%m-%d'),
        'destination': req.destination,
        'customer_name': req.customer_name,
        'leaving_time': req.leaving_time.strftime('%H:%M'),
        'return_time': req.return_time.strftime('%H:%M') if req.return_time else None,
    } for req in pending]

    return JsonResponse({
        'requests': requests_data,
        'count': len(requests_data)
    })


@login_required
def update_attendance(request):
    """API endpoint to update attendance records. Super admin only."""
    if not request.user.is_superuser:
        return JsonResponse({'error': 'Permission denied.'}, status=403)

    if request.method != 'POST':
        return JsonResponse({'error': 'Only POST method allowed'}, status=405)

    try:
        data = json.loads(request.body)
        employee_id = data.get('employee_id')
        date_str = data.get('date')
        first_in = data.get('first_in')
        last_out = data.get('last_out')
        is_work_from_home = bool(data.get('is_work_from_home', False))
        is_paid_leave = bool(data.get('is_paid_leave', False))

        if not employee_id or not date_str:
            return JsonResponse({'error': 'Missing required fields: employee_id, date'}, status=400)

        record_date = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()

        try:
            employee = Employee.objects.get(id=employee_id)
        except Employee.DoesNotExist:
            return JsonResponse({'error': 'Employee not found'}, status=404)

        first_in_time = datetime.datetime.strptime(first_in, '%H:%M').time() if first_in else None
        last_out_time = datetime.datetime.strptime(last_out, '%H:%M').time() if last_out else None

        work_duration = None
        if first_in_time and last_out_time:
            first_dt = datetime.datetime.combine(record_date, first_in_time)
            last_dt = datetime.datetime.combine(record_date, last_out_time)
            work_duration = max(last_dt - first_dt, timedelta(0))

        record, created = AttendanceRecord.objects.update_or_create(
            employee=employee,
            date=record_date,
            defaults={
                'first_in': first_in_time,
                'last_out': last_out_time,
                'work_duration': work_duration,
                'is_work_from_home': is_work_from_home,
                'is_paid_leave': is_paid_leave,
            }
        )

        recalculate_monthly_summary(employee, record_date.year, record_date.month)

        logger.info(
            "Attendance updated by %s: employee=%s date=%s",
            request.user.username, employee.name, date_str
        )

        return JsonResponse({
            'success': True,
            'message': 'Attendance updated successfully',
            'data': {
                'employee_id': employee.id,
                'date': date_str,
                'first_in': first_in,
                'last_out': last_out,
                'work_duration': str(work_duration) if work_duration else None,
                'is_work_from_home': is_work_from_home,
                'is_paid_leave': is_paid_leave,
            }
        })

    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON data'}, status=400)
    except ValueError as e:
        return JsonResponse({'error': f'Invalid data format: {e}'}, status=400)
    except Exception:
        logger.exception("Error updating attendance")
        return JsonResponse({'error': 'An internal error occurred.'}, status=500)


def recalculate_monthly_summary(employee, year, month):
    """Recalculate monthly summary for an employee after attendance edit."""
    records = AttendanceRecord.objects.filter(
        employee=employee,
        date__year=year,
        date__month=month
    ).order_by('date')

    month_start = datetime.date(year, month, 1)
    shift_start, shift_end = get_employee_shift_for_date(employee, month_start)
    sat_shift_start, sat_shift_end = get_saturday_shift()

    working_days = records.count()
    late_days = 0
    arrived_after_noon_days = 0
    grace_uses = 0  # tracks how many times the 10-min grace has been used this month

    for record in records:
        is_saturday = record.date.weekday() == 5
        if is_saturday:
            day_shift_start, day_shift_end = sat_shift_start, sat_shift_end
        else:
            day_shift_start, day_shift_end = shift_start, shift_end

        # 10-minute grace period: allowed max 3 times per month
        grace_cutoff = (
            datetime.datetime.combine(datetime.date.min, day_shift_start)
            + datetime.timedelta(minutes=10)
        ).time()
        # Compare using hour:minute only (ignore seconds)
        fi_hm = (record.first_in.hour, record.first_in.minute) if record.first_in else None
        shift_hm = (day_shift_start.hour, day_shift_start.minute)
        grace_hm = (grace_cutoff.hour, grace_cutoff.minute)
        in_grace_window = (
            fi_hm is not None and
            fi_hm > shift_hm and
            fi_hm <= grace_hm
        )
        if in_grace_window:
            grace_uses += 1
            is_late = grace_uses > 3
        else:
            is_late = bool(fi_hm is not None and fi_hm > shift_hm)
        if is_late:
            late_days += 1

        if record.first_in and record.first_in.hour >= 12 and not is_saturday:
            arrived_after_noon_days += 1

    _, days_in_month = calendar.monthrange(year, month)
    total_workdays = sum(
        1 for day in range(1, days_in_month + 1)
        if datetime.date(year, month, day).weekday() != 6
    )

    leave_days = max(0, total_workdays - working_days)

    # Add Sundays and custom holidays within AnnualLeave periods so the
    # stored leave_days matches the full calendar duration of the leave.
    from ..models import Holiday
    month_end = datetime.date(year, month, days_in_month)
    holiday_dates = set(
        Holiday.objects.filter(
            date__year=year, date__month=month
        ).values_list('date', flat=True)
    )
    for al in AnnualLeave.objects.filter(
        employee=employee,
        start_date__lte=month_end,
        end_date__gte=month_start,
    ):
        curr = max(al.start_date, month_start)
        end_al = min(al.end_date, month_end)
        while curr <= end_al:
            if curr.weekday() == 6 or curr in holiday_dates:
                leave_days += 1
            curr += datetime.timedelta(days=1)

    MonthlySummary.objects.update_or_create(
        employee=employee,
        year=year,
        month=month,
        defaults={
            'working_days': working_days,
            'leave_days': leave_days,
            'late_days': late_days,
            'half_days': arrived_after_noon_days
        }
    )


@login_required
@user_passes_test(section_required('on_duty_requests'), login_url='/report/')
def get_request_attendance_data(request, request_id):
    """Get attendance data for a pending early leave request."""
    try:
        early_leave = EarlyLeaveRequest.objects.select_related(
            'employee', 'remote_employee'
        ).get(id=request_id)
    except EarlyLeaveRequest.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Request not found'})

    request_date = early_leave.request_date

    if early_leave.employee:
        attendance = AttendanceRecord.objects.filter(
            employee=early_leave.employee,
            date=request_date
        ).first()

        has_data = attendance is not None
        first_in = attendance.first_in.strftime('%H:%M') if attendance and attendance.first_in else ''
        last_out = attendance.last_out.strftime('%H:%M') if attendance and attendance.last_out else ''
        employee_name = early_leave.employee.name
        employee_type = 'inhouse'
    else:
        call_record = RemoteCallRecord.objects.filter(
            employee=early_leave.remote_employee,
            date=request_date
        ).first()

        has_data = call_record is not None
        first_in = ''
        last_out = ''
        employee_name = early_leave.remote_employee.name
        employee_type = 'remote'

    return JsonResponse({
        'success': True,
        'has_data': has_data,
        'employee_name': employee_name,
        'employee_type': employee_type,
        'request_date': request_date.strftime('%Y-%m-%d'),
        'first_in': first_in,
        'last_out': last_out,
        'leaving_time': early_leave.leaving_time.strftime('%H:%M'),
        'return_time': early_leave.return_time.strftime('%H:%M') if early_leave.return_time else '',
        'destination': early_leave.destination,
        'customer_name': early_leave.customer_name,
        'reason': early_leave.reason,
    })


@login_required
@user_passes_test(section_required('on_duty_requests'), login_url='/report/')
def approve_early_leave(request, request_id):
    """Approve an early leave request and update attendance times."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    try:
        early_leave = EarlyLeaveRequest.objects.get(id=request_id)
    except EarlyLeaveRequest.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Request not found'})

    if early_leave.status != 'pending':
        return JsonResponse({'success': False, 'error': 'Request already processed'})

    if early_leave.employee:
        new_first_in = request.POST.get('new_first_in', '').strip()
        new_last_out = request.POST.get('new_last_out', '').strip()

        try:
            first_in_time = datetime.datetime.strptime(new_first_in, '%H:%M').time() if new_first_in else None
            last_out_time = datetime.datetime.strptime(new_last_out, '%H:%M').time() if new_last_out else None

            if not last_out_time and early_leave.return_time:
                last_out_time = early_leave.return_time

            early_leave.approved_first_in = first_in_time
            early_leave.approved_last_out = last_out_time

            attendance, created = AttendanceRecord.objects.get_or_create(
                employee=early_leave.employee,
                date=early_leave.request_date,
                defaults={
                    'first_in': first_in_time,
                    'last_out': last_out_time,
                    'work_duration': None
                }
            )

            if not created:
                if first_in_time:
                    attendance.first_in = first_in_time
                if last_out_time:
                    attendance.last_out = last_out_time

            if attendance.first_in and attendance.last_out:
                today = datetime.date.today()
                dt_in = datetime.datetime.combine(today, attendance.first_in)
                dt_out = datetime.datetime.combine(today, attendance.last_out)
                attendance.work_duration = max(dt_out - dt_in, timedelta(0))

            attendance.save()

        except ValueError:
            return JsonResponse({'success': False, 'error': 'Invalid time format'})

    early_leave.status = 'approved'
    early_leave.reviewed_at = timezone.now()
    early_leave.save()

    logger.info(
        "Early leave approved: request_id=%s by %s",
        request_id, request.user.username
    )
    return JsonResponse({'success': True, 'message': 'Request approved successfully'})


@login_required
@user_passes_test(section_required('on_duty_requests'), login_url='/report/')
def decline_early_leave(request, request_id):
    """Decline an early leave request."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    try:
        early_leave = EarlyLeaveRequest.objects.get(id=request_id)
    except EarlyLeaveRequest.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Request not found'})

    if early_leave.status != 'pending':
        return JsonResponse({'success': False, 'error': 'Request already processed'})

    admin_notes = request.POST.get('admin_notes', '').strip()

    early_leave.status = 'rejected'
    early_leave.admin_notes = admin_notes
    early_leave.reviewed_at = timezone.now()
    early_leave.save()

    logger.info(
        "Early leave declined: request_id=%s by %s",
        request_id, request.user.username
    )
    return JsonResponse({'success': True, 'message': 'Request declined'})


@login_required
@user_passes_test(section_required('on_duty_requests'), login_url='/report/')
def approve_all_on_duty(request):
    """Bulk-approve all pending on-duty requests using each request's own times."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    pending = EarlyLeaveRequest.objects.filter(status='pending').select_related(
        'employee', 'remote_employee'
    )
    now = timezone.now()
    approved_count = 0

    for early_leave in pending:
        if early_leave.employee:
            first_in_time = early_leave.leaving_time
            last_out_time = early_leave.return_time

            early_leave.approved_first_in = first_in_time
            early_leave.approved_last_out = last_out_time

            attendance, created = AttendanceRecord.objects.get_or_create(
                employee=early_leave.employee,
                date=early_leave.request_date,
                defaults={
                    'first_in': first_in_time,
                    'last_out': last_out_time,
                    'work_duration': None,
                }
            )
            if not created:
                if first_in_time:
                    attendance.first_in = first_in_time
                if last_out_time:
                    attendance.last_out = last_out_time

            if attendance.first_in and attendance.last_out:
                today = datetime.date.today()
                dt_in = datetime.datetime.combine(today, attendance.first_in)
                dt_out = datetime.datetime.combine(today, attendance.last_out)
                attendance.work_duration = max(dt_out - dt_in, timedelta(0))

            attendance.save()

        early_leave.status = 'approved'
        early_leave.reviewed_at = now
        early_leave.save()
        approved_count += 1

    logger.info(
        "Bulk on-duty approval: %d requests approved by %s",
        approved_count, request.user.username
    )
    return JsonResponse({'success': True, 'approved_count': approved_count})


@login_required
@user_passes_test(superuser_required, login_url='/report/')
def update_remote_attendance(request):
    """API endpoint to update a remote call record. Super admin only."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Only POST method allowed'}, status=405)

    try:
        data = json.loads(request.body)
        employee_id = data.get('employee_id')
        date_str = data.get('date')
        talk_minutes = data.get('talk_minutes')
        answered_calls = data.get('answered_calls', 0)

        if not employee_id or not date_str:
            return JsonResponse({'error': 'Missing required fields: employee_id, date'}, status=400)

        record_date = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()

        try:
            employee = RemoteEmployee.objects.get(id=employee_id)
        except RemoteEmployee.DoesNotExist:
            return JsonResponse({'error': 'Employee not found'}, status=404)

        talk_minutes_int = int(talk_minutes) if talk_minutes is not None else 0
        talk_duration = datetime.timedelta(minutes=talk_minutes_int) if talk_minutes_int > 0 else None

        record, created = RemoteCallRecord.objects.get_or_create(
            employee=employee,
            date=record_date,
            defaults={
                'total_talk_duration': talk_duration,
                'answered_calls': int(answered_calls),
            }
        )
        if not created:
            record.total_talk_duration = talk_duration
            record.answered_calls = int(answered_calls)
            record.save()

        logger.info(
            "Remote attendance updated by %s: employee=%s date=%s talk_min=%s",
            request.user.username, employee.name, date_str, talk_minutes_int
        )

        return JsonResponse({
            'success': True,
            'message': 'Remote attendance updated successfully',
            'data': {
                'employee_id': employee.id,
                'date': date_str,
                'talk_minutes': talk_minutes_int,
                'answered_calls': int(answered_calls),
                'status': record.attendance_status,
            }
        })

    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON data'}, status=400)
    except ValueError as e:
        return JsonResponse({'error': f'Invalid data format: {e}'}, status=400)
    except Exception:
        logger.exception("Error updating remote attendance")
        return JsonResponse({'error': 'An internal error occurred.'}, status=500)
