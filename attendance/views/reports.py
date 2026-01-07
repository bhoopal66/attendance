"""
Report views for attendance data visualization.
Handles both in-house and remote employee attendance reports.
"""

import datetime
import calendar
from datetime import time, timedelta
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch

from ..models import (
    Employee, AttendanceRecord, MonthlySummary, ShiftHistory, Holiday,
    RemoteEmployee, RemoteCallRecord, RemoteMonthlySummary, EarlyLeaveRequest,
    LeaveRequest
)


@login_required
def attendance_report(request):
    """Display attendance report for in-house employees."""
    # Get current date
    now = datetime.datetime.now()
    
    # Get filter params or default to current
    try:
        selected_month = int(request.GET.get('month', now.month))
        selected_year = int(request.GET.get('year', now.year))
    except ValueError:
        selected_month = now.month
        selected_year = now.year

    # Filter records
    records_qs = AttendanceRecord.objects.filter(
        date__year=selected_year, 
        date__month=selected_month
    ).order_by('date')

    # Check if we should show inactive employees
    show_inactive = request.GET.get('show_inactive', '') == '1'
    
    # Get search query
    search_query = request.GET.get('search', '').strip()
    
    # Fetch employees with filtered records
    employees_qs = Employee.objects.prefetch_related(
        Prefetch('attendancerecord_set', queryset=records_qs, to_attr='filtered_records')
    )
    
    # Filter by active status unless show_inactive is checked
    if not show_inactive:
        employees_qs = employees_qs.filter(is_active=True)
    
    # Filter by search query if provided
    if search_query:
        employees_qs = employees_qs.filter(name__icontains=search_query)
    
    employees = employees_qs.order_by('name')

    # Calendar data
    first_weekday, days_in_month = calendar.monthrange(selected_year, selected_month)
    first_weekday_sunday = (first_weekday + 1) % 7

    # Build list of day numbers for calendar grid
    calendar_days = [None] * first_weekday_sunday + list(range(1, days_in_month + 1))
    while len(calendar_days) % 7 != 0:
        calendar_days.append(None)

    # Calculate expected working days
    today = datetime.date.today()
    if selected_year == today.year and selected_month == today.month:
        calculation_end_day = today.day
    elif (selected_year < today.year) or (selected_year == today.year and selected_month < today.month):
        calculation_end_day = days_in_month
    else:
        calculation_end_day = 0  # Future month
    
    # Query custom holidays for this month
    month_start = datetime.date(selected_year, selected_month, 1)
    month_end = datetime.date(selected_year, selected_month, days_in_month)
    holidays_in_month = Holiday.objects.filter(date__gte=month_start, date__lte=month_end)
    holiday_dates = set(h.date for h in holidays_in_month)
    holiday_names = {h.date: h.name for h in holidays_in_month}
    
    # Calculate Sundays and custom holidays up to the calculation end day
    sundays_until_now = 0
    holidays_until_now = 0
    for day in range(1, calculation_end_day + 1):
        date = datetime.date(selected_year, selected_month, day)
        if date.weekday() == 6:  # Sunday
            sundays_until_now += 1
        elif date in holiday_dates:
            holidays_until_now += 1
    
    total_holidays_until_now = sundays_until_now + holidays_until_now
    expected_working_days = calculation_end_day - sundays_until_now - holidays_until_now
    
    current_day = today.day if selected_year == today.year and selected_month == today.month else 32
    
    for employee in employees:
        employee.calendar_data = {}
        late_count = 0
        half_day_count = 0
        actual_working_days_count = 0
        
        # Get employee's shift timings
        applicable_shift = ShiftHistory.objects.filter(
            employee=employee,
            effective_from__lte=month_start
        ).order_by('-effective_from').first()
        
        if applicable_shift:
            emp_shift_start = applicable_shift.shift_start
            emp_shift_end = applicable_shift.shift_end
        elif employee.shift_start and employee.shift_end:
            emp_shift_start = employee.shift_start
            emp_shift_end = employee.shift_end
        else:
            emp_shift_start = time(10, 0)
            emp_shift_end = time(19, 0)
        
        # Calculate expected work hours
        shift_duration_weekday = (
            (emp_shift_end.hour * 60 + emp_shift_end.minute) - 
            (emp_shift_start.hour * 60 + emp_shift_start.minute)
        ) * 60
        
        sat_shift_end = time(emp_shift_start.hour + 4, emp_shift_start.minute)

        records_dict = {r.date.day: r for r in employee.filtered_records}
        
        # Get approved leaves for this employee and month
        approved_leaves = LeaveRequest.objects.filter(
            employee=employee,
            status='approved',
            start_date__lte=month_end,
            end_date__gte=month_start
        )
        approved_leave_days = set()
        for leave in approved_leaves:
            start = max(leave.start_date, month_start)
            end = min(leave.end_date, month_end)
            curr = start
            while curr <= end:
                approved_leave_days.add(curr.day)
                curr += datetime.timedelta(days=1)
        
        paid_leave_count = 0
        
        for day in range(1, days_in_month + 1):
            date_obj = datetime.date(selected_year, selected_month, day)
            weekday = date_obj.weekday()
            is_sunday = weekday == 6
            is_saturday = weekday == 5
            is_holiday_date = date_obj in holiday_dates
            is_paid_leave = day in approved_leave_days
            
            record = records_dict.get(day)
            
            status = 'absent'
            is_half_day = False
            is_late = False
            
            if is_paid_leave:
                status = 'paid_leave'
                if not is_sunday and not is_holiday_date:
                    paid_leave_count += 1
            elif is_sunday:
                status = 'holiday'
            elif is_holiday_date:
                status = 'holiday'
            elif record:
                total_secs = record.work_duration.total_seconds() if record.work_duration else 0
                
                # Check for incomplete records (only check-in or only checkout)
                has_first_in = record.first_in is not None
                has_last_out = record.last_out is not None
                is_incomplete = (has_first_in and not has_last_out) or (has_last_out and not has_first_in)
                
                if total_secs > 0 and not is_sunday:
                    actual_working_days_count += 1
                
                arrived_after_noon = record.first_in and record.first_in.hour >= 12
                
                if is_saturday:
                    hours_ok = total_secs >= 14400
                    time_in_ok = record.first_in and (
                        record.first_in.hour < emp_shift_start.hour or 
                        (record.first_in.hour == emp_shift_start.hour and record.first_in.minute <= emp_shift_start.minute)
                    )
                    time_out_ok = record.last_out and (
                        record.last_out.hour > sat_shift_end.hour or 
                        (record.last_out.hour == sat_shift_end.hour and record.last_out.minute >= sat_shift_end.minute)
                    )
                else:
                    hours_ok = total_secs >= shift_duration_weekday
                    time_in_ok = record.first_in and (
                        record.first_in.hour < emp_shift_start.hour or 
                        (record.first_in.hour == emp_shift_start.hour and record.first_in.minute <= emp_shift_start.minute)
                    )
                    time_out_ok = record.last_out and (
                        record.last_out.hour > emp_shift_end.hour or 
                        (record.last_out.hour == emp_shift_end.hour and record.last_out.minute >= emp_shift_end.minute)
                    )
                
                if not is_sunday and record.first_in and not time_in_ok and not arrived_after_noon:
                    late_count += 1
                    is_late = True
                
                if not is_sunday and total_secs > 0:
                    if arrived_after_noon:
                        is_half_day = True
                    elif not time_out_ok:
                        is_half_day = True
                
                if is_half_day:
                    half_day_count += 1
                
                # Determine status - prioritize incomplete status for HR attention
                if is_incomplete:
                    status = 'incomplete'  # New status for incomplete records
                elif total_secs == 0:
                    status = 'absent'
                elif is_half_day:
                    status = 'yellow'
                elif hours_ok and time_in_ok and time_out_ok:
                    status = 'green'
                else:
                    status = 'yellow'
            else:
                is_incomplete = False
                if day > current_day:
                    pass
                else:
                    status = 'absent'

            employee.calendar_data[day] = {
                'record': record,
                'status': status,
                'is_sunday': is_sunday,
                'is_saturday': is_saturday,
                'is_half_day': is_half_day,
                'is_late': is_late,
                'is_holiday': is_holiday_date,
                'is_paid_leave': is_paid_leave,
                'is_incomplete': is_incomplete if record else False,
            }
        
        full_days = actual_working_days_count - half_day_count
        if full_days < 0:
            full_days = 0
        
        leave_days = expected_working_days - actual_working_days_count
        if leave_days < 0:
            leave_days = 0
        
        # Paid leave days count as working days for payment, but we track them separately for display
        total_working = full_days + (half_day_count * 0.5) + total_holidays_until_now + paid_leave_count
        
        # Adjust leave days (absent days) to not include paid leave days
        leave_days = leave_days - paid_leave_count
        if leave_days < 0:
            leave_days = 0
        
        employee.summary = {
            'working_days': actual_working_days_count,
            'paid_leaves': paid_leave_count, # Add for display
            'full_days': full_days,
            'half_days': half_day_count,
            'total_working': total_working,
            'sundays': sundays_until_now,
            'holidays': holidays_until_now,
            'leave_days': leave_days,
            'late_days': late_count,
        }
        
        MonthlySummary.objects.update_or_create(
            employee=employee,
            year=selected_year,
            month=selected_month,
            defaults={
                'working_days': actual_working_days_count, # Store actual worked days
                'leave_days': leave_days,
                'late_days': late_count,
                'half_days': half_day_count,
            }
        )

    # Get pending early leave requests for admin view
    pending_requests = EarlyLeaveRequest.objects.filter(status='pending').select_related('employee', 'remote_employee')
    pending_count = pending_requests.count()

    context = {
        'employees': employees,
        'selected_month': selected_month,
        'selected_year': selected_year,
        'current_day': current_day,
        'months': [
            (1, 'January'), (2, 'February'), (3, 'March'), (4, 'April'),
            (5, 'May'), (6, 'June'), (7, 'July'), (8, 'August'),
            (9, 'September'), (10, 'October'), (11, 'November'), (12, 'December')
        ],
        'years': range(2020, 2036),
        'calendar_days': calendar_days,
        'weekdays': ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'],
        'days_in_month': days_in_month,
        'show_inactive': show_inactive,
        'search_query': search_query,
        'holiday_days': [h.date.day for h in holidays_in_month],
        'holiday_names': {h.date.day: h.name for h in holidays_in_month},
        'pending_requests': pending_requests,
        'pending_count': pending_count,
    }
    return render(request, 'attendance/report.html', context)


@login_required
def remote_attendance_report(request):
    """Display attendance report for remote team."""
    now = datetime.datetime.now()
    
    try:
        selected_month = int(request.GET.get('month', now.month))
        selected_year = int(request.GET.get('year', now.year))
    except ValueError:
        selected_month = now.month
        selected_year = now.year
    
    records_qs = RemoteCallRecord.objects.filter(
        date__year=selected_year,
        date__month=selected_month
    ).order_by('date')
    
    show_inactive = request.GET.get('show_inactive', '') == '1'
    search_query = request.GET.get('search', '').strip()
    
    employees_qs = RemoteEmployee.objects.prefetch_related(
        Prefetch('remotecallrecord_set', queryset=records_qs, to_attr='filtered_records')
    )
    
    if not show_inactive:
        employees_qs = employees_qs.filter(is_active=True)
    
    if search_query:
        employees_qs = employees_qs.filter(name__icontains=search_query)
    
    employees = employees_qs.order_by('name')
    
    first_weekday, days_in_month = calendar.monthrange(selected_year, selected_month)
    first_weekday_sunday = (first_weekday + 1) % 7
    
    calendar_days = [None] * first_weekday_sunday + list(range(1, days_in_month + 1))
    while len(calendar_days) % 7 != 0:
        calendar_days.append(None)
    
    today = datetime.date.today()
    if selected_year == today.year and selected_month == today.month:
        calculation_end_day = today.day
    elif (selected_year < today.year) or (selected_year == today.year and selected_month < today.month):
        calculation_end_day = days_in_month
    else:
        calculation_end_day = 0
    
    month_start = datetime.date(selected_year, selected_month, 1)
    month_end = datetime.date(selected_year, selected_month, days_in_month)
    holidays_in_month = Holiday.objects.filter(date__gte=month_start, date__lte=month_end)
    holiday_dates = set(h.date for h in holidays_in_month)
    
    sundays_until_now = 0
    holidays_until_now = 0
    for day in range(1, calculation_end_day + 1):
        date = datetime.date(selected_year, selected_month, day)
        if date.weekday() == 6:
            sundays_until_now += 1
        elif date in holiday_dates:
            holidays_until_now += 1
    
    total_holidays_until_now = sundays_until_now + holidays_until_now
    expected_working_days = calculation_end_day - sundays_until_now - holidays_until_now
    current_day = today.day if selected_year == today.year and selected_month == today.month else 32
    
    for employee in employees:
        employee.calendar_data = {}
        present_count = 0
        half_day_count = 0
        absent_count = 0
        total_talk_seconds = 0
        
        records_dict = {r.date.day: r for r in employee.filtered_records}
        
        for day in range(1, days_in_month + 1):
            date_obj = datetime.date(selected_year, selected_month, day)
            weekday = date_obj.weekday()
            is_sunday = weekday == 6
            is_saturday = weekday == 5
            is_holiday_date = date_obj in holiday_dates
            
            record = records_dict.get(day)
            
            status = 'absent'
            talk_minutes = 0
            answered_calls = 0
            
            if is_sunday:
                status = 'holiday'
            elif is_holiday_date:
                status = 'holiday'
            elif record:
                if record.total_talk_duration:
                    talk_minutes = int(record.total_talk_duration.total_seconds() / 60)
                    total_talk_seconds += record.total_talk_duration.total_seconds()
                
                answered_calls = record.answered_calls
                status = record.attendance_status
                
                if not is_sunday:
                    if record.attendance_status == 'present':
                        present_count += 1
                    elif record.attendance_status == 'half_day':
                        half_day_count += 1
                    elif record.attendance_status == 'absent':
                        absent_count += 1
            else:
                if day > current_day:
                    status = 'absent'
                else:
                    status = 'absent'
            
            employee.calendar_data[day] = {
                'record': record,
                'status': status,
                'is_sunday': is_sunday,
                'is_saturday': is_saturday,
                'is_holiday': is_holiday_date,
                'talk_minutes': talk_minutes,
                'answered_calls': answered_calls,
            }
        
        days_with_records = len([r for r in employee.filtered_records if r.date.weekday() != 6])
        total_absent = expected_working_days - days_with_records - present_count - half_day_count
        if total_absent < 0:
            total_absent = 0
        absent_count += total_absent
        
        employee.summary = {
            'present_days': present_count,
            'half_days': half_day_count,
            'absent_days': absent_count,
            'total_talk_hours': round(total_talk_seconds / 3600, 1),
        }
        
        RemoteMonthlySummary.objects.update_or_create(
            employee=employee,
            year=selected_year,
            month=selected_month,
            defaults={
                'present_days': present_count,
                'half_days': half_day_count,
                'absent_days': absent_count,
                'total_talk_time': timedelta(seconds=total_talk_seconds),
            }
        )
    
    context = {
        'employees': employees,
        'selected_month': selected_month,
        'selected_year': selected_year,
        'current_day': current_day,
        'months': [
            (1, 'January'), (2, 'February'), (3, 'March'), (4, 'April'),
            (5, 'May'), (6, 'June'), (7, 'July'), (8, 'August'),
            (9, 'September'), (10, 'October'), (11, 'November'), (12, 'December')
        ],
        'years': range(2020, 2036),
        'calendar_days': calendar_days,
        'weekdays': ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'],
        'days_in_month': days_in_month,
        'show_inactive': show_inactive,
        'search_query': search_query,
        'holiday_days': [h.date.day for h in holidays_in_month],
        'holiday_names': {h.date.day: h.name for h in holidays_in_month},
    }
    return render(request, 'attendance/remote_report.html', context)
