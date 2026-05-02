"""
Employee self-service portal views.
Handles employee login, logout, portal view, and leave request submission.
"""

import datetime
import logging

from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.contrib.auth.hashers import check_password, make_password

from ..models import (
    AnnualLeave, AttendanceRecord, EarlyLeaveRequest, Employee, Holiday,
    LeaveRequest, RemoteCallRecord, RemoteEmployee,
)
from .utils import (
    MONTH_CHOICES, MONTH_NAMES, WEEKDAY_HEADERS, YEAR_RANGE,
    build_calendar_grid, get_active_special_periods_for_month,
    get_approved_leave_days,
    get_employee_shift_for_date, get_holiday_data,
    get_remote_thresholds_from_period, get_saturday_shift,
    get_selected_month_year,
)

logger = logging.getLogger('attendance')


def _authenticate_employee(email, password):
    """
    Try to authenticate an employee by email and password.
    Checks both in-house and remote employees.

    For linked employees (same email exists in both tables with a matching password),
    the Employee record's location field decides which portal to use:
      - location == 'remote' (case-insensitive) → remote portal
      - anything else                            → in-house portal

    Returns: (employee_object, employee_type_string) or (None, None)
    """
    inhouse_match = None
    remote_match = None

    for emp in Employee.objects.filter(email__iexact=email, is_active=True):
        if emp.portal_password and check_password(password, emp.portal_password):
            inhouse_match = emp
            break

    for emp in RemoteEmployee.objects.filter(email__iexact=email, is_active=True):
        if emp.portal_password and check_password(password, emp.portal_password):
            remote_match = emp
            break

    # Linked employee: both records match — use location on the in-house record to decide
    if inhouse_match and remote_match:
        location = (inhouse_match.location or '').strip().lower()
        if location == 'remote':
            return remote_match, 'remote'
        return inhouse_match, 'inhouse'

    if inhouse_match:
        return inhouse_match, 'inhouse'
    if remote_match:
        return remote_match, 'remote'

    return None, None


def employee_login(request):
    """Login page for employee portal (separate from admin login)."""
    if request.session.get('employee_id'):
        return redirect('employee_portal')

    error_message = None

    if request.method == 'POST':
        email = request.POST.get('email', '').strip().lower()
        password = request.POST.get('password', '')

        if not email or not password:
            error_message = "Please enter both email and password."
        else:
            employee, employee_type = _authenticate_employee(email, password)

            if employee:
                request.session['employee_id'] = employee.id
                request.session['employee_type'] = employee_type
                request.session['employee_name'] = employee.name
                logger.info("Employee portal login: %s (%s)", employee.name, employee_type)
                return redirect('employee_portal')
            else:
                logger.warning("Failed portal login attempt for email: %s", email)
                error_message = "Invalid email or password."

    return render(request, 'attendance/employee_login.html', {'error_message': error_message})


def employee_logout(request):
    """Logout from employee portal."""
    employee_name = request.session.get('employee_name', 'Unknown')
    for key in ('employee_id', 'employee_type', 'employee_name'):
        request.session.pop(key, None)
    logger.info("Employee portal logout: %s", employee_name)
    return redirect('employee_login')


def _get_portal_employee(request):
    """Get the logged-in portal employee. Returns (employee, type) or redirects."""
    employee_id = request.session.get('employee_id')
    employee_type = request.session.get('employee_type')
    if not employee_id or not employee_type:
        return None, None
    return employee_id, employee_type


def _build_inhouse_portal_data(employee, selected_year, selected_month, cal_data,
                               holiday_dates, current_day):
    """Build calendar and summary data for an in-house employee portal view."""
    month_start = cal_data['month_start']
    month_end = cal_data['month_end']
    days_in_month = cal_data['days_in_month']

    records = AttendanceRecord.objects.filter(
        employee=employee,
        date__year=selected_year,
        date__month=selected_month
    )
    records_dict = {r.date.day: r for r in records}

    approved_leave_days = get_approved_leave_days(employee, month_start, month_end)
    shift_start, shift_end = get_employee_shift_for_date(employee, month_start)
    sat_shift_start, sat_shift_end = get_saturday_shift()

    calendar_data = {}
    summary = {
        'full_days': 0, 'leave_days': 0, 'late_days': 0,
        'half_days': 0, 'holidays': 0, 'paid_leave_days': 0
    }

    for day in range(1, days_in_month + 1):
        date = datetime.date(selected_year, selected_month, day)
        weekday = date.weekday()
        is_sunday = weekday == 6
        is_holiday = date in holiday_dates
        is_paid_leave = day in approved_leave_days

        if (is_sunday or is_holiday) and day <= current_day and not is_paid_leave:
            summary['holidays'] += 1

        record = records_dict.get(day)

        if is_paid_leave:
            calendar_data[day] = {
                'record': None, 'status': 'paid_leave',
                'is_sunday': is_sunday, 'is_holiday': is_holiday
            }
            summary['paid_leave_days'] += 1
        elif is_sunday or is_holiday:
            calendar_data[day] = {
                'record': None, 'status': 'holiday',
                'is_sunday': is_sunday, 'is_holiday': is_holiday
            }
        elif record:
            total_secs = record.work_duration.total_seconds() if record.work_duration else 0
            is_saturday = weekday == 5

            arrived_after_noon = record.first_in and record.first_in.hour >= 12 and not is_saturday

            if is_saturday:
                is_late = record.first_in and (
                    record.first_in.hour > sat_shift_start.hour or
                    (record.first_in.hour == sat_shift_start.hour and
                     record.first_in.minute > sat_shift_start.minute)
                )
                left_early = record.last_out and (
                    record.last_out.hour < sat_shift_end.hour or
                    (record.last_out.hour == sat_shift_end.hour and
                     record.last_out.minute < sat_shift_end.minute)
                )
            else:
                is_late = record.first_in and (
                    record.first_in.hour > shift_start.hour or
                    (record.first_in.hour == shift_start.hour and
                     record.first_in.minute > shift_start.minute)
                )
                left_early = record.last_out and (
                    record.last_out.hour < shift_end.hour or
                    (record.last_out.hour == shift_end.hour and
                     record.last_out.minute < shift_end.minute)
                )

            if total_secs == 0:
                status = 'absent'
                summary['leave_days'] += 1
            elif arrived_after_noon:
                status = 'yellow'
                summary['half_days'] += 1
                if is_late:
                    summary['late_days'] += 1
            elif is_late:
                status = 'yellow'
                summary['late_days'] += 1
                summary['full_days'] += 1
            else:
                status = 'green'
                summary['full_days'] += 1

            calendar_data[day] = {
                'record': record, 'status': status,
                'is_sunday': False, 'is_holiday': False
            }
        elif day < current_day:
            calendar_data[day] = {
                'record': None, 'status': 'absent',
                'is_sunday': False, 'is_holiday': False
            }
            summary['leave_days'] += 1

    late_half_days = summary['late_days'] // 3
    summary['late_half_days'] = late_half_days

    # Count Sundays/holidays inside AnnualLeave periods — normally shown as
    # 'holiday' in the calendar but should count toward the leave total so the
    # displayed number matches the full calendar duration of the leave.
    for al in AnnualLeave.objects.filter(
        employee=employee,
        start_date__lte=month_end,
        end_date__gte=month_start,
    ):
        curr = max(al.start_date, month_start)
        end = min(al.end_date, month_end)
        while curr <= end:
            if curr.weekday() == 6 or curr in holiday_dates:
                summary['leave_days'] += 1
            curr += datetime.timedelta(days=1)

    summary['total_deductions'] = (
        summary['leave_days'] + (summary['half_days'] * 0.5) +
        (late_half_days * 0.5)
    )
    return calendar_data, summary


def _build_remote_portal_data(employee, selected_year, selected_month, cal_data,
                              holiday_dates, current_day, special_periods=None):
    """Build calendar and summary data for a remote employee portal view."""
    days_in_month = cal_data['days_in_month']

    records = RemoteCallRecord.objects.filter(
        employee=employee,
        date__year=selected_year,
        date__month=selected_month
    )
    records_dict = {r.date.day: r for r in records}

    calendar_data = {}
    summary = {'present_days': 0, 'half_days': 0, 'absent_days': 0, 'total_talk_hours': 0, 'holidays': 0}
    total_talk_seconds = 0

    for day in range(1, days_in_month + 1):
        date = datetime.date(selected_year, selected_month, day)
        weekday = date.weekday()
        is_sunday = weekday == 6
        is_holiday = date in holiday_dates

        if (is_sunday or is_holiday) and day <= current_day:
            summary['holidays'] += 1

        record = records_dict.get(day)

        if is_sunday or is_holiday:
            calendar_data[day] = {
                'record': None, 'status': 'holiday',
                'is_sunday': is_sunday, 'is_holiday': is_holiday
            }
        elif record:
            talk_minutes = int(record.total_talk_duration.total_seconds() / 60) if record.total_talk_duration else 0
            total_talk_seconds += record.total_talk_duration.total_seconds() if record.total_talk_duration else 0

            # Check for special shift period with remote thresholds
            active_period = None
            if special_periods:
                active_period = next(
                    (p for p in special_periods if p.start_date <= date <= p.end_date),
                    None
                )
            remote_thresholds = get_remote_thresholds_from_period(active_period) if active_period else None

            if remote_thresholds:
                att_status = record.calculate_attendance_status(thresholds=remote_thresholds)
            else:
                att_status = record.attendance_status

            if att_status == 'present':
                status = 'green'
                summary['present_days'] += 1
            elif att_status == 'half_day':
                status = 'yellow'
                summary['half_days'] += 1
            else:
                status = 'absent'
                summary['absent_days'] += 1

            calendar_data[day] = {
                'record': record, 'status': status,
                'is_sunday': False, 'is_holiday': False,
                'talk_minutes': talk_minutes,
                'answered_calls': record.answered_calls
            }
        elif day < current_day:
            calendar_data[day] = {
                'record': None, 'status': 'absent',
                'is_sunday': False, 'is_holiday': False
            }
            summary['absent_days'] += 1

    summary['total_talk_hours'] = round(total_talk_seconds / 3600, 1)
    summary['total_deductions'] = summary['absent_days'] + (summary['half_days'] * 0.5)
    return calendar_data, summary


def employee_portal(request):
    """Employee portal - shows only the logged-in employee's attendance calendar."""
    employee_id, employee_type = _get_portal_employee(request)
    if not employee_id:
        return redirect('employee_login')

    employee_name = request.session.get('employee_name')
    selected_month, selected_year = get_selected_month_year(request)

    cal_data = build_calendar_grid(selected_year, selected_month)
    month_start = cal_data['month_start']
    month_end = cal_data['month_end']

    holiday_dates, _, holidays_qs = get_holiday_data(month_start, month_end)
    holiday_days = [h.date.day for h in holidays_qs]

    today = datetime.date.today()
    current_day = today.day if selected_year == today.year and selected_month == today.month else 32

    if employee_type == 'inhouse':
        try:
            employee = Employee.objects.get(id=employee_id)
        except Employee.DoesNotExist:
            return redirect('employee_logout')

        calendar_data, summary = _build_inhouse_portal_data(
            employee, selected_year, selected_month, cal_data, holiday_dates, current_day
        )
        context = {
            'employee': employee,
            'employee_type': 'In-House',
            'calendar_data': calendar_data,
            'summary': summary,
        }
    else:
        try:
            employee = RemoteEmployee.objects.get(id=employee_id)
        except RemoteEmployee.DoesNotExist:
            return redirect('employee_logout')

        special_periods = get_active_special_periods_for_month(month_start, month_end)
        calendar_data, summary = _build_remote_portal_data(
            employee, selected_year, selected_month, cal_data, holiday_dates, current_day,
            special_periods=special_periods
        )
        context = {
            'employee': employee,
            'employee_type': 'Remote',
            'calendar_data': calendar_data,
            'summary': summary,
        }

    context.update({
        'employee_name': employee_name,
        'selected_month': selected_month,
        'selected_year': selected_year,
        'month_name': MONTH_NAMES[selected_month],
        'months': MONTH_CHOICES,
        'years': YEAR_RANGE,
        'calendar_days': cal_data['calendar_days'],
        'weekdays': WEEKDAY_HEADERS,
        'days_in_month': cal_data['days_in_month'],
        'current_day': current_day,
        'holiday_days': holiday_days,
    })

    return render(request, 'attendance/employee_portal.html', context)


def submit_early_leave_request(request):
    """Handle early leave request submission from employee portal."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'}, status=405)

    if 'employee_id' not in request.session:
        return JsonResponse({'success': False, 'error': 'Not logged in'}, status=401)

    employee_id = request.session.get('employee_id')
    employee_type = request.session.get('employee_type')

    leaving_time_str = request.POST.get('leaving_time')
    return_time_str = request.POST.get('return_time')
    destination = request.POST.get('destination', '').strip()
    customer_name = request.POST.get('customer_name', '').strip()
    reason = request.POST.get('reason', '').strip()

    if not leaving_time_str or not return_time_str or not destination or not customer_name:
        return JsonResponse({
            'success': False,
            'error': 'Please fill in all required fields (leaving time, return time, destination, customer name)'
        })

    try:
        leaving_time = datetime.datetime.strptime(leaving_time_str, '%H:%M').time()
        return_time = datetime.datetime.strptime(return_time_str, '%H:%M').time() if return_time_str else None
    except ValueError:
        return JsonResponse({'success': False, 'error': 'Invalid time format'})

    try:
        early_leave = EarlyLeaveRequest(
            request_date=datetime.date.today(),
            leaving_time=leaving_time,
            return_time=return_time,
            destination=destination,
            customer_name=customer_name,
            reason=reason,
            status='pending'
        )

        if employee_type == 'inhouse':
            early_leave.employee_id = employee_id
        else:
            early_leave.remote_employee_id = employee_id

        early_leave.save()
        logger.info("Early leave request submitted by employee_id=%s", employee_id)
        return JsonResponse({'success': True, 'message': 'Request submitted successfully'})
    except Exception:
        logger.exception("Error submitting early leave request for employee_id=%s", employee_id)
        return JsonResponse({'success': False, 'error': 'Failed to submit request. Please try again.'})


def submit_leave_request(request):
    """Handle leave request submission from employee portal."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'}, status=405)

    if 'employee_id' not in request.session:
        return JsonResponse({'success': False, 'error': 'Not logged in'}, status=401)

    employee_id = request.session.get('employee_id')
    employee_type = request.session.get('employee_type')

    if employee_type != 'inhouse':
        return JsonResponse({'success': False, 'error': 'Leave requests are only available for in-house employees'})

    leave_type = request.POST.get('leave_type', '').strip()
    start_date_str = request.POST.get('start_date', '').strip()
    end_date_str = request.POST.get('end_date', '').strip()
    reason = request.POST.get('reason', '').strip()
    document = request.FILES.get('document')

    valid_leave_types = ('sick', 'medical', 'annual', 'casual')
    if leave_type not in valid_leave_types:
        return JsonResponse({'success': False, 'error': 'Please select a valid leave type'})

    if not start_date_str or not end_date_str:
        return JsonResponse({'success': False, 'error': 'Please select start and end dates'})

    if not reason:
        return JsonResponse({'success': False, 'error': 'Please provide a reason for your leave request'})

    if leave_type == 'medical' and not document:
        return JsonResponse({'success': False, 'error': 'Medical leave requires a supporting document'})

    try:
        start_date = datetime.datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end_date = datetime.datetime.strptime(end_date_str, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({'success': False, 'error': 'Invalid date format'})

    if end_date < start_date:
        return JsonResponse({'success': False, 'error': 'End date cannot be before start date'})

    requested_days = (end_date - start_date).days + 1

    try:
        leave_request = LeaveRequest(
            employee_id=employee_id,
            leave_type=leave_type,
            start_date=start_date,
            end_date=end_date,
            reason=reason,
            requested_days=requested_days,
            status='pending'
        )

        if document:
            leave_request.document = document

        leave_request.save()
        logger.info("Leave request submitted by employee_id=%s: %s", employee_id, leave_type)
        return JsonResponse({'success': True, 'message': 'Leave request submitted successfully'})
    except Exception:
        logger.exception("Error submitting leave request for employee_id=%s", employee_id)
        return JsonResponse({'success': False, 'error': 'Failed to submit request. Please try again.'})


def get_my_requests(request):
    """API endpoint to get the logged-in employee's own requests."""
    if 'employee_id' not in request.session:
        return JsonResponse({
            'on_duty': [], 'leave': [],
            'on_duty_has_more': False, 'leave_has_more': False
        })

    employee_id = request.session.get('employee_id')
    employee_type = request.session.get('employee_type')

    try:
        on_duty_offset = max(0, int(request.GET.get('on_duty_offset', 0)))
        leave_offset = max(0, int(request.GET.get('leave_offset', 0)))
        limit = min(50, max(1, int(request.GET.get('limit', 5))))
    except (ValueError, TypeError):
        on_duty_offset = 0
        leave_offset = 0
        limit = 5

    # Get on-duty requests
    if employee_type == 'inhouse':
        on_duty_qs = EarlyLeaveRequest.objects.filter(employee_id=employee_id)
    else:
        on_duty_qs = EarlyLeaveRequest.objects.filter(remote_employee_id=employee_id)

    total_on_duty = on_duty_qs.count()
    on_duty_page = on_duty_qs.order_by('-created_at')[on_duty_offset:on_duty_offset + limit]
    on_duty_has_more = (on_duty_offset + limit) < total_on_duty

    on_duty_requests = [{
        'id': req.id,
        'request_date': req.request_date.strftime('%Y-%m-%d'),
        'destination': req.destination,
        'customer_name': req.customer_name,
        'leaving_time': req.leaving_time.strftime('%H:%M'),
        'return_time': req.return_time.strftime('%H:%M') if req.return_time else None,
        'status': req.status,
        'created_at': req.created_at.strftime('%Y-%m-%d %H:%M'),
    } for req in on_duty_page]

    # Get leave requests (in-house only)
    leave_requests = []
    leave_has_more = False
    if employee_type == 'inhouse':
        leave_qs = LeaveRequest.objects.filter(employee_id=employee_id)
        total_leave = leave_qs.count()
        leave_page = leave_qs.order_by('-created_at')[leave_offset:leave_offset + limit]
        leave_has_more = (leave_offset + limit) < total_leave

        leave_requests = [{
            'id': leave.id,
            'leave_type': leave.get_leave_type_display(),
            'start_date': leave.start_date.strftime('%Y-%m-%d'),
            'end_date': leave.end_date.strftime('%Y-%m-%d'),
            'requested_days': leave.requested_days,
            'approved_days': leave.approved_days,
            'reason': leave.reason[:100] + '...' if len(leave.reason) > 100 else leave.reason,
            'status': leave.status,
            'admin_notes': leave.admin_notes if leave.status == 'rejected' else '',
            'created_at': leave.created_at.strftime('%Y-%m-%d %H:%M'),
        } for leave in leave_page]

    return JsonResponse({
        'on_duty': on_duty_requests,
        'leave': leave_requests,
        'on_duty_has_more': on_duty_has_more,
        'leave_has_more': leave_has_more
    })


def employee_change_password(request):
    """Allow a logged-in portal employee to change their own password."""
    employee_id = request.session.get('employee_id')
    employee_type = request.session.get('employee_type')
    if not employee_id or not employee_type:
        return redirect('employee_login')

    success = False
    error_message = None

    if request.method == 'POST':
        current_password = request.POST.get('current_password', '')
        new_password = request.POST.get('new_password', '')
        confirm_password = request.POST.get('confirm_password', '')

        if not current_password or not new_password or not confirm_password:
            error_message = "Please fill in all fields."
        elif new_password != confirm_password:
            error_message = "New passwords do not match."
        elif len(new_password) < 8:
            error_message = "New password must be at least 8 characters."
        else:
            try:
                if employee_type == 'inhouse':
                    employee = Employee.objects.get(id=employee_id, is_active=True)
                else:
                    employee = RemoteEmployee.objects.get(id=employee_id, is_active=True)

                if not employee.portal_password or not check_password(current_password, employee.portal_password):
                    error_message = "Current password is incorrect."
                else:
                    employee.portal_password = make_password(new_password)
                    employee.save(update_fields=['portal_password'])
                    logger.info("Password changed for employee_id=%s (%s)", employee_id, employee_type)
                    success = True
            except (Employee.DoesNotExist, RemoteEmployee.DoesNotExist):
                return redirect('employee_logout')

    return render(request, 'attendance/employee_change_password.html', {
        'employee_name': request.session.get('employee_name'),
        'employee_type': 'In-House' if employee_type == 'inhouse' else 'Remote',
        'success': success,
        'error_message': error_message,
    })
