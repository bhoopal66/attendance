"""
Report views for attendance data visualization.
Handles both in-house and remote employee attendance reports.
"""

import datetime
import logging

from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.shortcuts import render

from ..models import (
    AttendanceRecord, EarlyLeaveRequest, Employee, RemoteCallRecord, RemoteEmployee,
)
from .utils import (
    MONTH_NAMES, SATURDAY_WORK_DURATION_SECONDS,
    build_calendar_grid, count_holidays_in_range,
    get_bulk_approved_leave_days, get_bulk_employee_shifts,
    get_active_special_periods_for_month, get_common_report_context,
    get_holiday_data, get_saturday_shift,
    get_selected_month_year,
)

logger = logging.getLogger('attendance')


def _compute_inhouse_calendar(employee, days_in_month, selected_year, selected_month,
                              holiday_dates, current_day, emp_shift_start, emp_shift_end,
                              sat_shift_start, sat_shift_end, approved_leave_days,
                              special_periods=None, today_on_duty_request=None):
    """Compute calendar data and summary for an in-house employee."""
    records_dict = {r.date.day: r for r in employee.filtered_records}

    calendar_data = {}
    late_count = 0
    half_day_count = 0
    actual_working_days_count = 0
    paid_leave_count = 0
    grace_uses = 0

    shift_duration_weekday = (
        (emp_shift_end.hour * 60 + emp_shift_end.minute) -
        (emp_shift_start.hour * 60 + emp_shift_start.minute)
    ) * 60

    for day in range(1, days_in_month + 1):
        date_obj = datetime.date(selected_year, selected_month, day)
        weekday = date_obj.weekday()
        is_sunday = weekday == 6
        is_saturday = weekday == 5
        is_holiday_date = date_obj in holiday_dates
        is_paid_leave = day in approved_leave_days

        # Determine if a special shift period covers this day
        active_period = None
        if special_periods:
            active_period = next(
                (p for p in special_periods if p.start_date <= date_obj <= p.end_date),
                None
            )

        # Resolve effective shift for this day
        if is_saturday:
            if active_period and active_period.sat_shift_start:
                day_shift_start = active_period.sat_shift_start
                day_shift_end = active_period.sat_shift_end
            else:
                day_shift_start = sat_shift_start
                day_shift_end = sat_shift_end
        elif not is_sunday:
            if active_period:
                day_shift_start = active_period.shift_start
                day_shift_end = active_period.shift_end
            else:
                day_shift_start = emp_shift_start
                day_shift_end = emp_shift_end
        else:
            day_shift_start = day_shift_end = None

        day_shift_duration = (
            (day_shift_end.hour * 60 + day_shift_end.minute) -
            (day_shift_start.hour * 60 + day_shift_start.minute)
        ) * 60 if day_shift_start and day_shift_end else 0

        record = records_dict.get(day)

        status = ''
        is_half_day = False
        is_late = False
        is_incomplete = False
        is_grace = False

        if is_paid_leave:
            status = 'paid_leave'
            if not is_sunday and not is_holiday_date:
                paid_leave_count += 1
        elif is_sunday or is_holiday_date:
            status = 'holiday'
        elif record:
            total_secs = record.work_duration.total_seconds() if record.work_duration else 0

            has_first_in = record.first_in is not None
            has_last_out = record.last_out is not None
            is_incomplete = (has_first_in and not has_last_out) or (has_last_out and not has_first_in)

            if total_secs > 0 and not is_sunday:
                actual_working_days_count += 1

            arrived_after_noon = record.first_in and record.first_in.hour >= 12 and not is_saturday

            hours_ok = total_secs >= day_shift_duration
            grace_cutoff = (
                datetime.datetime.combine(datetime.date.min, day_shift_start)
                + datetime.timedelta(minutes=10)
            ).time() if day_shift_start else None
            in_grace_window = bool(
                record.first_in and day_shift_start and grace_cutoff and
                record.first_in > day_shift_start and
                record.first_in <= grace_cutoff
            )
            if in_grace_window:
                grace_uses += 1
                if grace_uses <= 3:
                    is_grace = True
                    time_in_ok = True
                else:
                    time_in_ok = False
            else:
                time_in_ok = bool(record.first_in and day_shift_start and record.first_in <= day_shift_start)
            time_out_ok = record.last_out and day_shift_end and (
                record.last_out.hour > day_shift_end.hour or
                (record.last_out.hour == day_shift_end.hour and
                 record.last_out.minute >= day_shift_end.minute)
            )

            if not is_sunday and record.first_in and not time_in_ok and not arrived_after_noon:
                late_count += 1
                is_late = True

            if not is_sunday and total_secs > 0:
                if arrived_after_noon:
                    is_half_day = True

            if is_half_day:
                half_day_count += 1

            if is_incomplete:
                status = 'incomplete'
            elif total_secs == 0:
                status = 'absent'
            elif is_half_day:
                status = 'yellow'
            elif hours_ok and time_in_ok and time_out_ok:
                status = 'green'
            else:
                status = 'yellow'
        else:
            if day < current_day:
                status = 'absent'
            elif day == current_day and today_on_duty_request:
                status = 'on_duty'

        calendar_data[day] = {
            'record': record,
            'status': status,
            'is_sunday': is_sunday,
            'is_saturday': is_saturday,
            'is_half_day': is_half_day,
            'is_late': is_late,
            'is_grace': is_grace,
            'is_holiday': is_holiday_date,
            'is_paid_leave': is_paid_leave,
            'is_incomplete': is_incomplete if record else False,
            'on_duty_request': today_on_duty_request if status == 'on_duty' else None,
        }

    return calendar_data, {
        'actual_working_days': actual_working_days_count,
        'late_count': late_count,
        'half_day_count': half_day_count,
        'paid_leave_count': paid_leave_count,
    }


@login_required
def attendance_report(request):
    """Display attendance report for in-house employees."""
    selected_month, selected_year = get_selected_month_year(request)

    records_qs = AttendanceRecord.objects.filter(
        date__year=selected_year,
        date__month=selected_month
    ).order_by('date')

    show_inactive = request.GET.get('show_inactive', '') == '1'
    search_query = request.GET.get('search', '').strip()

    employees_qs = Employee.objects.prefetch_related(
        Prefetch('attendancerecord_set', queryset=records_qs, to_attr='filtered_records')
    )

    if not show_inactive:
        employees_qs = employees_qs.filter(is_active=True)
    if search_query:
        employees_qs = employees_qs.filter(name__icontains=search_query)

    employees = employees_qs.order_by('name')

    cal_data = build_calendar_grid(selected_year, selected_month)
    days_in_month = cal_data['days_in_month']
    month_start = cal_data['month_start']
    month_end = cal_data['month_end']

    holiday_dates, holiday_names, holidays_qs = get_holiday_data(month_start, month_end)

    today = datetime.date.today()
    is_current_month = selected_year == today.year and selected_month == today.month
    if is_current_month:
        # Exclude today: biometric data is only uploaded the following day
        calculation_end_day = today.day - 1
    elif (selected_year < today.year) or (selected_year == today.year and selected_month < today.month):
        calculation_end_day = days_in_month
    else:
        calculation_end_day = 0

    sundays_until_now, holidays_until_now, total_holidays_until_now, expected_working_days = \
        count_holidays_in_range(selected_year, selected_month, calculation_end_day, holiday_dates)

    current_day = today.day if is_current_month else 32

    sat_shift_start, sat_shift_end = get_saturday_shift()
    special_periods = get_active_special_periods_for_month(month_start, month_end)

    employees = list(employees)
    bulk_shifts = get_bulk_employee_shifts(employees, month_start)
    bulk_leave_days = get_bulk_approved_leave_days(employees, month_start, month_end)

    # Fetch today's approved on-duty requests (for preview when no biometric data yet)
    today_on_duty_map = {}
    if is_current_month:
        on_duty_qs = EarlyLeaveRequest.objects.filter(
            employee_id__in=[e.id for e in employees],
            request_date=today,
            status='approved',
            approved_first_in__isnull=False,
        )
        today_on_duty_map = {r.employee_id: r for r in on_duty_qs}

    for employee in employees:
        emp_shift_start, emp_shift_end = bulk_shifts[employee.id]
        approved_leave_days = bulk_leave_days[employee.id]

        employee.calendar_data, stats = _compute_inhouse_calendar(
            employee, days_in_month, selected_year, selected_month,
            holiday_dates, current_day, emp_shift_start, emp_shift_end,
            sat_shift_start, sat_shift_end, approved_leave_days,
            special_periods=special_periods,
            today_on_duty_request=today_on_duty_map.get(employee.id),
        )

        actual_working = stats['actual_working_days']
        half_days = stats['half_day_count']
        paid_leaves = stats['paid_leave_count']
        late_count = stats['late_count']

        full_days = max(0, actual_working - half_days)
        leave_days = max(0, expected_working_days - actual_working - paid_leaves)
        late_half_days = late_count // 3
        total_deductions = leave_days + (half_days * 0.5) + (late_half_days * 0.5)

        employee.summary = {
            'paid_leaves': paid_leaves,
            'full_days': full_days,
            'half_days': half_days,
            'leave_days': leave_days,
            'late_days': late_count,
            'late_half_days': late_half_days,
            'total_deductions': total_deductions,
        }

    # Team-level summary for KPI cards
    team_summary = {
        'total_employees': len(employees),
        'total_deductions': sum(e.summary['total_deductions'] for e in employees),
        'total_leave_days': sum(e.summary['leave_days'] for e in employees),
        'total_late_days': sum(e.summary['late_days'] for e in employees),
    } if employees else {}

    pending_requests = EarlyLeaveRequest.objects.filter(
        status='pending'
    ).select_related('employee', 'remote_employee')

    context = get_common_report_context(
        selected_month, selected_year, cal_data, holidays_qs,
        show_inactive, search_query
    )
    context.update({
        'employees': employees,
        'pending_requests': pending_requests,
        'pending_count': pending_requests.count(),
        'team_summary': team_summary,
        'month_name': MONTH_NAMES[selected_month],
    })
    return render(request, 'attendance/report.html', context)


@login_required
def remote_attendance_report(request):
    """Display attendance report for remote team."""
    selected_month, selected_year = get_selected_month_year(request)

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

    cal_data = build_calendar_grid(selected_year, selected_month)
    days_in_month = cal_data['days_in_month']
    month_start = cal_data['month_start']
    month_end = cal_data['month_end']

    holiday_dates, holiday_names, holidays_qs = get_holiday_data(month_start, month_end)

    today = datetime.date.today()
    is_current_month = selected_year == today.year and selected_month == today.month
    if is_current_month:
        # Exclude today: call data is only uploaded the following day
        calculation_end_day = today.day - 1
    elif (selected_year < today.year) or (selected_year == today.year and selected_month < today.month):
        calculation_end_day = days_in_month
    else:
        calculation_end_day = 0

    sundays_until_now, holidays_until_now, total_holidays_until_now, expected_working_days = \
        count_holidays_in_range(selected_year, selected_month, calculation_end_day, holiday_dates)

    current_day = today.day if is_current_month else 32

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

            status = ''
            talk_minutes = 0
            answered_calls = 0

            if is_sunday or is_holiday_date:
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

            elif day < current_day:
                # No record for this past working day — count as absent
                status = 'absent'
                absent_count += 1

            employee.calendar_data[day] = {
                'record': record,
                'status': status,
                'is_sunday': is_sunday,
                'is_saturday': is_saturday,
                'is_holiday': is_holiday_date,
                'talk_minutes': talk_minutes,
                'answered_calls': answered_calls,
            }

        employee.summary = {
            'present_days': present_count,
            'half_days': half_day_count,
            'absent_days': absent_count,
            'total_talk_hours': round(total_talk_seconds / 3600, 1),
        }

    context = get_common_report_context(
        selected_month, selected_year, cal_data, holidays_qs,
        show_inactive, search_query
    )
    context['employees'] = employees
    return render(request, 'attendance/remote_report.html', context)
