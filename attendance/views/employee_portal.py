"""
Employee self-service portal views.
Handles employee login, logout, portal view, and leave request submission.
"""

import datetime
import calendar
from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.contrib.auth.hashers import check_password

from ..models import (
    Employee, AttendanceRecord, RemoteEmployee, RemoteCallRecord,
    Holiday, EarlyLeaveRequest, LeaveRequest
)


def employee_login(request):
    """Login page for employee portal (separate from admin login)."""
    # If already logged in as employee, redirect to portal
    if request.session.get('employee_id'):
        return redirect('employee_portal')
    
    error_message = None
    
    if request.method == 'POST':
        email = request.POST.get('email', '').strip().lower()
        password = request.POST.get('password', '')
        
        if not email or not password:
            error_message = "Please enter both email and password."
        else:
            # Try to find employee by email (check both models)
            employee = None
            employee_type = None
            
            # Check in-house employees
            try:
                emp = Employee.objects.get(email__iexact=email, is_active=True)
                if emp.portal_password:
                    if check_password(password, emp.portal_password):
                        employee = emp
                        employee_type = 'inhouse'
            except Employee.DoesNotExist:
                pass
            except Employee.MultipleObjectsReturned:
                emp = Employee.objects.filter(email__iexact=email, is_active=True).first()
                if emp and emp.portal_password and check_password(password, emp.portal_password):
                    employee = emp
                    employee_type = 'inhouse'
            
            # If not found, check remote employees
            if not employee:
                try:
                    remote_emp = RemoteEmployee.objects.get(email__iexact=email, is_active=True)
                    if remote_emp.portal_password:
                        if check_password(password, remote_emp.portal_password):
                            employee = remote_emp
                            employee_type = 'remote'
                except RemoteEmployee.DoesNotExist:
                    pass
                except RemoteEmployee.MultipleObjectsReturned:
                    remote_emp = RemoteEmployee.objects.filter(email__iexact=email, is_active=True).first()
                    if remote_emp and remote_emp.portal_password and check_password(password, remote_emp.portal_password):
                        employee = remote_emp
                        employee_type = 'remote'
            
            if employee:
                # Store in session
                request.session['employee_id'] = employee.id
                request.session['employee_type'] = employee_type
                request.session['employee_name'] = employee.name
                return redirect('employee_portal')
            else:
                error_message = "Invalid email or password."
    
    return render(request, 'attendance/employee_login.html', {'error_message': error_message})


def employee_logout(request):
    """Logout from employee portal."""
    if 'employee_id' in request.session:
        del request.session['employee_id']
    if 'employee_type' in request.session:
        del request.session['employee_type']
    if 'employee_name' in request.session:
        del request.session['employee_name']
    return redirect('employee_login')


def employee_portal(request):
    """Employee portal - shows only the logged-in employee's attendance calendar."""
    employee_id = request.session.get('employee_id')
    employee_type = request.session.get('employee_type')
    employee_name = request.session.get('employee_name')
    
    if not employee_id or not employee_type:
        return redirect('employee_login')
    
    now = datetime.datetime.now()
    
    try:
        selected_month = int(request.GET.get('month', now.month))
        selected_year = int(request.GET.get('year', now.year))
    except ValueError:
        selected_month = now.month
        selected_year = now.year
    
    first_weekday, days_in_month = calendar.monthrange(selected_year, selected_month)
    first_weekday_sunday = (first_weekday + 1) % 7
    
    calendar_days = [None] * first_weekday_sunday + list(range(1, days_in_month + 1))
    while len(calendar_days) % 7 != 0:
        calendar_days.append(None)
    
    month_start = datetime.date(selected_year, selected_month, 1)
    month_end = datetime.date(selected_year, selected_month, days_in_month)
    holidays_in_month = Holiday.objects.filter(date__gte=month_start, date__lte=month_end)
    holiday_dates = set(h.date for h in holidays_in_month)
    holiday_days = [h.date.day for h in holidays_in_month]
    
    today = datetime.date.today()
    current_day = today.day if selected_year == today.year and selected_month == today.month else 32
    
    month_names = ['', 'January', 'February', 'March', 'April', 'May', 'June',
                   'July', 'August', 'September', 'October', 'November', 'December']
    
    if employee_type == 'inhouse':
        try:
            employee = Employee.objects.get(id=employee_id)
        except Employee.DoesNotExist:
            return redirect('employee_logout')
        
        records = AttendanceRecord.objects.filter(
            employee=employee,
            date__year=selected_year,
            date__month=selected_month
        )
        records_dict = {r.date.day: r for r in records}
        
        # Get approved leaves for this month
        approved_leaves = LeaveRequest.objects.filter(
            employee=employee,
            status='approved',
            start_date__lte=month_end,
            end_date__gte=month_start
        )
        
        approved_leave_days = set()
        for leave in approved_leaves:
            # Calculate range intersection with current month
            start = max(leave.start_date, month_start)
            end = min(leave.end_date, month_end)
            curr = start
            while curr <= end:
                approved_leave_days.add(curr.day)
                curr += datetime.timedelta(days=1)
        
        default_shift_start = datetime.time(10, 0)
        default_shift_end = datetime.time(19, 0)
        shift_start = employee.shift_start or default_shift_start
        shift_end = employee.shift_end or default_shift_end
        
        calendar_data = {}
        summary = {'full_days': 0, 'leave_days': 0, 'late_days': 0, 'half_days': 0, 'holidays': 0, 'paid_leave_days': 0}
        
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
                    'record': None,
                    'status': 'paid_leave',
                    'is_sunday': is_sunday,
                    'is_holiday': is_holiday
                }
                summary['paid_leave_days'] += 1
            elif is_sunday or is_holiday:
                calendar_data[day] = {
                    'record': None,
                    'status': 'holiday',
                    'is_sunday': is_sunday,
                    'is_holiday': is_holiday
                }
            elif record:
                total_secs = record.work_duration.total_seconds() if record.work_duration else 0
                is_late = record.first_in and record.first_in > shift_start
                arrived_after_noon = record.first_in and record.first_in.hour >= 12
                
                is_saturday = weekday == 5
                if is_saturday:
                    sat_shift_end = datetime.time(shift_start.hour + 4, shift_start.minute)
                    left_early = record.last_out and record.last_out < sat_shift_end
                else:
                    left_early = record.last_out and record.last_out < shift_end
                
                if total_secs == 0:
                    status = 'absent'
                    summary['leave_days'] += 1
                elif arrived_after_noon or left_early:
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
                    'record': record,
                    'status': status,
                    'is_sunday': False,
                    'is_holiday': False
                }
            elif day <= current_day:
                calendar_data[day] = {
                    'record': None,
                    'status': 'absent',
                    'is_sunday': False,
                    'is_holiday': False
                }
                summary['leave_days'] += 1
        
        summary['total_working'] = summary['full_days'] + summary['holidays'] + (summary['half_days'] * 0.5) + summary['paid_leave_days']
        
        context = {
            'employee': employee,
            'employee_type': 'In-House',
            'calendar_data': calendar_data,
            'summary': summary,
        }
    
    else:  # Remote employee
        try:
            employee = RemoteEmployee.objects.get(id=employee_id)
        except RemoteEmployee.DoesNotExist:
            return redirect('employee_logout')
        
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
                    'record': None,
                    'status': 'holiday',
                    'is_sunday': is_sunday,
                    'is_holiday': is_holiday
                }
            elif record:
                talk_minutes = int(record.total_talk_duration.total_seconds() / 60) if record.total_talk_duration else 0
                total_talk_seconds += record.total_talk_duration.total_seconds() if record.total_talk_duration else 0
                
                if record.attendance_status == 'present':
                    status = 'green'
                    summary['present_days'] += 1
                elif record.attendance_status == 'half_day':
                    status = 'yellow'
                    summary['half_days'] += 1
                else:
                    status = 'absent'
                    summary['absent_days'] += 1
                
                calendar_data[day] = {
                    'record': record,
                    'status': status,
                    'is_sunday': False,
                    'is_holiday': False,
                    'talk_minutes': talk_minutes,
                    'answered_calls': record.answered_calls
                }
            elif day <= current_day:
                calendar_data[day] = {
                    'record': None,
                    'status': 'absent',
                    'is_sunday': False,
                    'is_holiday': False
                }
                summary['absent_days'] += 1
        
        summary['total_talk_hours'] = round(total_talk_seconds / 3600, 1)
        summary['total_working'] = summary['present_days'] + summary['holidays'] + (summary['half_days'] * 0.5)
        
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
        'month_name': month_names[selected_month],
        'months': [
            (1, 'January'), (2, 'February'), (3, 'March'), (4, 'April'),
            (5, 'May'), (6, 'June'), (7, 'July'), (8, 'August'),
            (9, 'September'), (10, 'October'), (11, 'November'), (12, 'December')
        ],
        'years': range(2020, 2036),
        'calendar_days': calendar_days,
        'weekdays': ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'],
        'days_in_month': days_in_month,
        'current_day': current_day,
        'holiday_days': holiday_days,
    })
    
    return render(request, 'attendance/employee_portal.html', context)


def submit_early_leave_request(request):
    """Handle early leave request submission from employee portal."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'})
    
    if 'employee_id' not in request.session:
        return JsonResponse({'success': False, 'error': 'Not logged in'})
    
    employee_id = request.session.get('employee_id')
    employee_type = request.session.get('employee_type')
    
    leaving_time_str = request.POST.get('leaving_time')
    return_time_str = request.POST.get('return_time')
    destination = request.POST.get('destination', '').strip()
    customer_name = request.POST.get('customer_name', '').strip()
    reason = request.POST.get('reason', '').strip()
    
    if not leaving_time_str or not return_time_str or not destination or not customer_name:
        return JsonResponse({'success': False, 'error': 'Please fill in all required fields (leaving time, return time, destination, customer name)'})
    
    try:
        leaving_time = datetime.datetime.strptime(leaving_time_str, '%H:%M').time()
        
        return_time = None
        if return_time_str:
            return_time = datetime.datetime.strptime(return_time_str, '%H:%M').time()
            
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
        
        return JsonResponse({'success': True, 'message': 'Request submitted successfully'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


def submit_leave_request(request):
    """Handle leave request submission from employee portal."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'})
    
    if 'employee_id' not in request.session:
        return JsonResponse({'success': False, 'error': 'Not logged in'})
    
    employee_id = request.session.get('employee_id')
    employee_type = request.session.get('employee_type')
    
    # Only in-house employees can apply for leave (for now)
    if employee_type != 'inhouse':
        return JsonResponse({'success': False, 'error': 'Leave requests are only available for in-house employees'})
    
    # Get form data
    leave_type = request.POST.get('leave_type', '').strip()
    start_date_str = request.POST.get('start_date', '').strip()
    end_date_str = request.POST.get('end_date', '').strip()
    reason = request.POST.get('reason', '').strip()
    document = request.FILES.get('document')
    
    # Validate required fields
    valid_leave_types = ['sick', 'medical', 'annual', 'casual']
    if leave_type not in valid_leave_types:
        return JsonResponse({'success': False, 'error': 'Please select a valid leave type'})
    
    if not start_date_str or not end_date_str:
        return JsonResponse({'success': False, 'error': 'Please select start and end dates'})
    
    if not reason:
        return JsonResponse({'success': False, 'error': 'Please provide a reason for your leave request'})
    
    # Check document requirement - only mandatory for medical leave
    if leave_type == 'medical' and not document:
        return JsonResponse({'success': False, 'error': 'Medical leave requires a supporting document'})
    
    # Parse dates
    try:
        start_date = datetime.datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end_date = datetime.datetime.strptime(end_date_str, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({'success': False, 'error': 'Invalid date format'})
    
    # Validate date range
    if end_date < start_date:
        return JsonResponse({'success': False, 'error': 'End date cannot be before start date'})
    
    # Calculate days
    requested_days = (end_date - start_date).days + 1
    
    # Create the leave request
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
        
        return JsonResponse({'success': True, 'message': 'Leave request submitted successfully'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


def get_my_requests(request):
    """API endpoint to get the logged-in employee's own requests for real-time display.
    
    Supports pagination with query parameters:
    - on_duty_offset: starting position for on-duty requests (default 0)
    - leave_offset: starting position for leave requests (default 0)
    - limit: number of items per request (default 5)
    """
    if 'employee_id' not in request.session:
        return JsonResponse({'on_duty': [], 'leave': [], 'on_duty_has_more': False, 'leave_has_more': False})
    
    employee_id = request.session.get('employee_id')
    employee_type = request.session.get('employee_type')
    
    # Pagination parameters
    try:
        on_duty_offset = int(request.GET.get('on_duty_offset', 0))
        leave_offset = int(request.GET.get('leave_offset', 0))
        limit = int(request.GET.get('limit', 5))
    except ValueError:
        on_duty_offset = 0
        leave_offset = 0
        limit = 5
    
    # Get on-duty requests (EarlyLeaveRequest)
    on_duty_requests = []
    if employee_type == 'inhouse':
        total_on_duty = EarlyLeaveRequest.objects.filter(employee_id=employee_id).count()
        requests_qs = EarlyLeaveRequest.objects.filter(employee_id=employee_id).order_by('-created_at')[on_duty_offset:on_duty_offset + limit]
    else:
        total_on_duty = EarlyLeaveRequest.objects.filter(remote_employee_id=employee_id).count()
        requests_qs = EarlyLeaveRequest.objects.filter(remote_employee_id=employee_id).order_by('-created_at')[on_duty_offset:on_duty_offset + limit]
    
    on_duty_has_more = (on_duty_offset + limit) < total_on_duty
    
    for req in requests_qs:
        on_duty_requests.append({
            'id': req.id,
            'request_date': req.request_date.strftime('%Y-%m-%d'),
            'destination': req.destination,
            'customer_name': req.customer_name,
            'leaving_time': req.leaving_time.strftime('%H:%M'),
            'return_time': req.return_time.strftime('%H:%M') if req.return_time else None,
            'status': req.status,
            'created_at': req.created_at.strftime('%Y-%m-%d %H:%M'),
        })
    
    # Get leave requests (in-house only)
    leave_requests = []
    leave_has_more = False
    if employee_type == 'inhouse':
        total_leave = LeaveRequest.objects.filter(employee_id=employee_id).count()
        leave_qs = LeaveRequest.objects.filter(employee_id=employee_id).order_by('-created_at')[leave_offset:leave_offset + limit]
        leave_has_more = (leave_offset + limit) < total_leave
        
        for leave in leave_qs:
            leave_requests.append({
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
            })
    
    return JsonResponse({
        'on_duty': on_duty_requests,
        'leave': leave_requests,
        'on_duty_has_more': on_duty_has_more,
        'leave_has_more': leave_has_more
    })


