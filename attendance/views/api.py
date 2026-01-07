"""
API endpoints for attendance management.
Handles attendance updates, request approval, and related API calls.
"""

import json
import datetime
import calendar
from datetime import time, timedelta
from django.http import JsonResponse
from django.utils import timezone
from django.contrib.auth.decorators import login_required, user_passes_test

from ..models import (
    Employee, AttendanceRecord, MonthlySummary, ShiftHistory,
    EarlyLeaveRequest, RemoteCallRecord
)
from .utils import superuser_required


@login_required
def update_attendance(request):
    """API endpoint to update attendance records. Super admin only."""
    # Only super admins can edit attendance
    if not request.user.is_superuser:
        return JsonResponse({'error': 'Permission denied. Super admin access required.'}, status=403)
    
    if request.method != 'POST':
        return JsonResponse({'error': 'Only POST method allowed'}, status=405)
    
    try:
        data = json.loads(request.body)
        employee_id = data.get('employee_id')
        date_str = data.get('date')  # Format: YYYY-MM-DD
        first_in = data.get('first_in')  # Format: HH:MM
        last_out = data.get('last_out')  # Format: HH:MM
        
        if not all([employee_id, date_str]):
            return JsonResponse({'error': 'Missing required fields: employee_id, date'}, status=400)
        
        # Parse date
        record_date = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
        
        # Get employee
        try:
            employee = Employee.objects.get(id=employee_id)
        except Employee.DoesNotExist:
            return JsonResponse({'error': 'Employee not found'}, status=404)
        
        # Parse times
        first_in_time = None
        last_out_time = None
        work_duration = None
        
        if first_in:
            first_in_time = datetime.datetime.strptime(first_in, '%H:%M').time()
        if last_out:
            last_out_time = datetime.datetime.strptime(last_out, '%H:%M').time()
        
        # Calculate work duration
        if first_in_time and last_out_time:
            first_dt = datetime.datetime.combine(record_date, first_in_time)
            last_dt = datetime.datetime.combine(record_date, last_out_time)
            if last_dt > first_dt:
                work_duration = last_dt - first_dt
            else:
                work_duration = timedelta(0)
        
        # Create or update attendance record
        record, created = AttendanceRecord.objects.update_or_create(
            employee=employee,
            date=record_date,
            defaults={
                'first_in': first_in_time,
                'last_out': last_out_time,
                'work_duration': work_duration
            }
        )
        
        # Recalculate monthly summary
        recalculate_monthly_summary(employee, record_date.year, record_date.month)
        
        return JsonResponse({
            'success': True,
            'message': 'Attendance updated successfully',
            'data': {
                'employee_id': employee.id,
                'date': date_str,
                'first_in': first_in,
                'last_out': last_out,
                'work_duration': str(work_duration) if work_duration else None
            }
        })
        
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON data'}, status=400)
    except ValueError as e:
        return JsonResponse({'error': f'Invalid data format: {str(e)}'}, status=400)
    except Exception as e:
        return JsonResponse({'error': f'Server error: {str(e)}'}, status=500)


def recalculate_monthly_summary(employee, year, month):
    """Recalculate monthly summary for an employee after attendance edit."""
    # Get all records for the month
    records = AttendanceRecord.objects.filter(
        employee=employee,
        date__year=year,
        date__month=month
    )
    
    # Get employee shift timings (default 10:00-19:00)
    default_shift_start = time(10, 0)
    default_shift_end = time(19, 0)
    shift_start = employee.shift_start or default_shift_start
    shift_end = employee.shift_end or default_shift_end
    
    # Count attendance metrics
    working_days = records.count()
    late_days = 0
    early_departure_days = 0
    
    for record in records:
        if record.first_in and record.first_in > shift_start:
            late_days += 1
        if record.last_out and record.last_out < shift_end:
            early_departure_days += 1
    
    # Calculate leave days (workdays minus working_days)
    first_day, days_in_month = calendar.monthrange(year, month)
    # Count weekdays (Mon-Sat, assuming Sunday is off)
    total_workdays = 0
    for day in range(1, days_in_month + 1):
        d = datetime.date(year, month, day)
        if d.weekday() != 6:  # Not Sunday
            total_workdays += 1
    
    leave_days = max(0, total_workdays - working_days)
    
    # Update or create summary
    MonthlySummary.objects.update_or_create(
        employee=employee,
        year=year,
        month=month,
        defaults={
            'working_days': working_days,
            'leave_days': leave_days,
            'late_days': late_days,
            'half_days': early_departure_days
        }
    )


@login_required
@user_passes_test(superuser_required, login_url='/report/')
def get_request_attendance_data(request, request_id):
    """Get attendance data for a pending early leave request."""
    try:
        early_leave = EarlyLeaveRequest.objects.get(id=request_id)
    except EarlyLeaveRequest.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Request not found'})
    
    request_date = early_leave.request_date
    
    if early_leave.employee:
        # In-house employee
        attendance = AttendanceRecord.objects.filter(
            employee=early_leave.employee,
            date=request_date
        ).first()
        
        if attendance:
            has_data = True
            first_in = attendance.first_in.strftime('%H:%M') if attendance.first_in else ''
            last_out = attendance.last_out.strftime('%H:%M') if attendance.last_out else ''
        else:
            has_data = False
            first_in = ''
            last_out = ''
        
        employee_name = early_leave.employee.name
        employee_type = 'inhouse'
    else:
        # Remote employee
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
@user_passes_test(superuser_required, login_url='/report/')
def approve_early_leave(request, request_id):
    """Approve an early leave request and update attendance times.
    
    Supports instant approval - can approve even without biometric data.
    If no attendance record exists, creates one with approved times.
    Stores approved times on the request for later merging with biometric data.
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'})
    
    try:
        early_leave = EarlyLeaveRequest.objects.get(id=request_id)
    except EarlyLeaveRequest.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Request not found'})
    
    if early_leave.status != 'pending':
        return JsonResponse({'success': False, 'error': 'Request already processed'})
    
    # Only in-house employees have time updates
    if early_leave.employee:
        # Get admin-provided times from the form
        new_first_in = request.POST.get('new_first_in', '').strip()
        new_last_out = request.POST.get('new_last_out', '').strip()
        
        try:
            # Parse times
            first_in_time = datetime.datetime.strptime(new_first_in, '%H:%M').time() if new_first_in else None
            last_out_time = datetime.datetime.strptime(new_last_out, '%H:%M').time() if new_last_out else None
            
            # If no checkout time provided, use return_time from the request
            if not last_out_time and early_leave.return_time:
                last_out_time = early_leave.return_time
            
            # Store approved times on the request for later merging with biometric data
            early_leave.approved_first_in = first_in_time
            early_leave.approved_last_out = last_out_time
            
            # Get or create attendance record
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
                # Record exists - update with admin times if provided
                if first_in_time:
                    attendance.first_in = first_in_time
                if last_out_time:
                    attendance.last_out = last_out_time
            
            # Recalculate work duration if both times exist
            if attendance.first_in and attendance.last_out:
                today = datetime.date.today()
                dt_in = datetime.datetime.combine(today, attendance.first_in)
                dt_out = datetime.datetime.combine(today, attendance.last_out)
                attendance.work_duration = dt_out - dt_in if dt_out > dt_in else timedelta(0)
            
            attendance.save()
            
        except ValueError:
            return JsonResponse({'success': False, 'error': 'Invalid time format'})
    
    # Mark request as approved
    early_leave.status = 'approved'
    early_leave.reviewed_at = timezone.now()
    early_leave.save()
    
    return JsonResponse({'success': True, 'message': 'Request approved successfully'})


@login_required
@user_passes_test(superuser_required, login_url='/report/')
def decline_early_leave(request, request_id):
    """Decline an early leave request."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'})
    
    try:
        early_leave = EarlyLeaveRequest.objects.get(id=request_id)
    except EarlyLeaveRequest.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Request not found'})
    
    if early_leave.status != 'pending':
        return JsonResponse({'success': False, 'error': 'Request already processed'})
    
    admin_notes = request.POST.get('admin_notes', '').strip()
    
    # Mark request as rejected
    early_leave.status = 'rejected'
    early_leave.admin_notes = admin_notes
    early_leave.reviewed_at = timezone.now()
    early_leave.save()
    
    return JsonResponse({'success': True, 'message': 'Request declined'})
