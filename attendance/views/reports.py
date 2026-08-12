"""
Report views for attendance data visualization.
Handles both in-house and remote employee attendance in a single unified report.
"""

import datetime
import logging

from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.shortcuts import redirect, render
from django.urls import reverse

from ..models import (
    AttendanceRecord, EarlyLeaveRequest, Employee, RemoteCallRecord, RemoteEmployee,
)
from .utils import (
    MONTH_NAMES, SATURDAY_WORK_DURATION_SECONDS,
    build_calendar_grid, count_holidays_in_range,
    get_bulk_annual_leave_non_working_days,
    get_bulk_approved_leave_days, get_bulk_employee_shifts,
    get_active_special_periods_for_month, get_common_report_context,
    get_holiday_data, get_remote_thresholds_from_period, get_saturday_shift,
    get_selected_month_year,
    remote_employee_uses_performance_v2, compute_sales_performance_v2_days,
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
        record = records_dict.get(day)
        is_paid_leave = day in approved_leave_days or bool(record and record.is_paid_leave)

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
        elif record and record.is_work_from_home:
            # WFH override: always counts as full day present
            actual_working_days_count += 1
            calendar_data[day] = {
                'record': record,
                'status': 'green',
                'is_wfh': True,
                'is_sunday': is_sunday,
                'is_saturday': is_saturday,
                'is_half_day': False,
                'is_late': False,
                'is_grace': False,
                'is_holiday': is_holiday_date,
                'is_paid_leave': is_paid_leave,
                'is_incomplete': False,
                'on_duty_request': None,
            }
            continue
        elif record:
            total_secs = record.work_duration.total_seconds() if record.work_duration else 0

            has_first_in = record.first_in is not None
            has_last_out = record.last_out is not None
            is_incomplete = (has_first_in and not has_last_out) or (has_last_out and not has_first_in)

            # Fixed salary: punch-in alone = present, skip all late/half-day/incomplete logic
            if employee.is_fixed_salary:
                if has_first_in:
                    actual_working_days_count += 1
                    status = 'green'
                else:
                    status = 'absent'
                calendar_data[day] = {
                    'record': record,
                    'status': status,
                    'is_sunday': is_sunday,
                    'is_saturday': is_saturday,
                    'is_half_day': False,
                    'is_late': False,
                    'is_grace': False,
                    'is_holiday': is_holiday_date,
                    'is_paid_leave': is_paid_leave,
                    'is_incomplete': False,
                    'on_duty_request': None,
                }
                continue

            if total_secs > 0 and not is_sunday:
                actual_working_days_count += 1

            arrived_after_noon = record.first_in and record.first_in.hour >= 12 and not is_saturday

            hours_ok = total_secs >= day_shift_duration
            grace_cutoff = (
                datetime.datetime.combine(datetime.date.min, day_shift_start)
                + datetime.timedelta(minutes=10)
            ).time() if day_shift_start else None
            # Compare using hour:minute only (ignore seconds)
            fi_hm = (record.first_in.hour, record.first_in.minute) if record.first_in else None
            shift_hm = (day_shift_start.hour, day_shift_start.minute) if day_shift_start else None
            grace_hm = (grace_cutoff.hour, grace_cutoff.minute) if grace_cutoff else None
            in_grace_window = bool(
                fi_hm and shift_hm and grace_hm and
                fi_hm > shift_hm and
                fi_hm <= grace_hm
            )
            if in_grace_window:
                grace_uses += 1
                if grace_uses <= 3:
                    is_grace = True
                    time_in_ok = True
                else:
                    time_in_ok = False
            else:
                time_in_ok = bool(fi_hm and shift_hm and fi_hm <= shift_hm)
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
            elif time_in_ok:
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
            'is_wfh': False,
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


def _compute_remote_calendar(employee, days_in_month, selected_year, selected_month,
                             holiday_dates, current_day, special_periods=None):
    """Compute calendar data and summary for a remote employee.

    Sales:Performance remote employees (attendance-based, not fixed-salary,
    salaried) whose pay for this month is governed by the "Method 2"
    talktime-proportional model use compute_sales_performance_v2_days for
    day classification, so the calendar always matches what payroll actually
    paid — see payroll._get_sales_performance_test_row for the full rule spec.
    """
    records_dict = {r.date.day: r for r in employee.filtered_records}

    calendar_data = {}
    present_count = 0
    half_day_count = 0
    proportional_count = 0
    absent_count = 0
    total_talk_seconds = 0

    v2_days = None
    if remote_employee_uses_performance_v2(employee, selected_year, selected_month):
        month_start = datetime.date(selected_year, selected_month, 1)
        month_end = datetime.date(selected_year, selected_month, days_in_month)
        v2_days = compute_sales_performance_v2_days(employee, month_start, month_end, holiday_dates)['days']

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
        grace_denial_reason = None

        if record:
            if record.total_talk_duration:
                talk_minutes = int(record.total_talk_duration.total_seconds() / 60)
                total_talk_seconds += record.total_talk_duration.total_seconds()
            answered_calls = record.answered_calls

        if v2_days is not None:
            day_info = v2_days.get(date_obj)
            if day_info and day_info['classification'] == 'non_working':
                status = 'holiday'
            elif day_info and (record or day < current_day):
                classification = day_info['classification']
                grace_denial_reason = day_info['grace_denial_reason']
                if classification == 'full':
                    status = 'present'
                    present_count += 1
                elif classification == 'proportional':
                    status = 'partial'
                    proportional_count += 1
                elif classification == 'half':
                    status = 'half_day'
                    half_day_count += 1
                else:
                    status = 'absent'
                    absent_count += 1
        elif is_sunday or is_holiday_date:
            status = 'holiday'
        elif record:
            # Check if a special shift period applies and has remote thresholds
            active_period = None
            if special_periods:
                active_period = next(
                    (p for p in special_periods if p.start_date <= date_obj <= p.end_date),
                    None
                )
            remote_thresholds = get_remote_thresholds_from_period(active_period) if active_period else None

            if remote_thresholds:
                status = record.calculate_attendance_status(thresholds=remote_thresholds)
            else:
                status = record.attendance_status

            if not is_sunday:
                if status == 'present':
                    present_count += 1
                elif status == 'half_day':
                    half_day_count += 1
                elif status == 'absent':
                    absent_count += 1

        elif day < current_day:
            # No record for this past working day — count as absent
            status = 'absent'
            absent_count += 1

        calendar_data[day] = {
            'record': record,
            'status': status,
            'is_sunday': is_sunday,
            'is_saturday': is_saturday,
            'is_holiday': is_holiday_date,
            'talk_minutes': talk_minutes,
            'answered_calls': answered_calls,
            'grace_denial_reason': grace_denial_reason,
        }

    return calendar_data, {
        'present_days': present_count,
        'half_days': half_day_count,
        'proportional_days': proportional_count,
        'absent_days': absent_count,
        'total_talk_hours': round(total_talk_seconds / 3600, 1),
    }


@login_required
def attendance_report(request):
    """
    Unified attendance report for in-house + remote employees.

    Employees linked via tcr_id appear once, using their in-house (fingerprint) calendar —
    the linked remote record is not shown separately since its call data is redundant once
    fingerprint attendance exists for that person.
    """
    selected_month, selected_year = get_selected_month_year(request)

    show_inactive = request.GET.get('show_inactive', '') == '1'
    search_query = request.GET.get('search', '').strip()
    type_filter = request.GET.get('type', '').strip().lower()
    if type_filter not in ('inhouse', 'remote'):
        type_filter = ''

    cal_data = build_calendar_grid(selected_year, selected_month)
    days_in_month = cal_data['days_in_month']
    month_start = cal_data['month_start']
    month_end = cal_data['month_end']

    holiday_dates, holiday_names, holidays_qs = get_holiday_data(month_start, month_end)
    special_periods = get_active_special_periods_for_month(month_start, month_end)

    today = datetime.date.today()
    is_current_month = selected_year == today.year and selected_month == today.month
    if is_current_month:
        # Exclude today: biometric/call data is only uploaded the following day
        calculation_end_day = today.day - 1
    elif (selected_year < today.year) or (selected_year == today.year and selected_month < today.month):
        calculation_end_day = days_in_month
    else:
        calculation_end_day = 0

    sundays_until_now, holidays_until_now, total_holidays_until_now, expected_working_days = \
        count_holidays_in_range(selected_year, selected_month, calculation_end_day, holiday_dates)

    current_day = today.day if is_current_month else 32

    # ── In-house employees ──────────────────────────────────────────────
    inhouse_records_qs = AttendanceRecord.objects.filter(
        date__year=selected_year,
        date__month=selected_month
    ).order_by('date')

    inhouse_qs = Employee.objects.prefetch_related(
        Prefetch('attendancerecord_set', queryset=inhouse_records_qs, to_attr='filtered_records')
    )
    if not show_inactive:
        inhouse_qs = inhouse_qs.filter(is_active=True)
    if search_query:
        inhouse_qs = inhouse_qs.filter(name__icontains=search_query)

    inhouse_employees = list(inhouse_qs.order_by('name'))

    sat_shift_start, sat_shift_end = get_saturday_shift()

    bulk_shifts = get_bulk_employee_shifts(inhouse_employees, month_start)
    bulk_leave_days = get_bulk_approved_leave_days(inhouse_employees, month_start, month_end)
    bulk_annual_leave_non_working = get_bulk_annual_leave_non_working_days(
        inhouse_employees, month_start, month_end, holiday_dates
    )

    # Fetch today's approved on-duty requests (for preview when no biometric data yet)
    today_on_duty_map = {}
    if is_current_month:
        on_duty_qs = EarlyLeaveRequest.objects.filter(
            employee_id__in=[e.id for e in inhouse_employees],
            request_date=today,
            status='approved',
            approved_first_in__isnull=False,
        )
        today_on_duty_map = {r.employee_id: r for r in on_duty_qs}

    # tcr_id links to dedupe remote employees who are already represented in-house
    linked_tcr_ids = {e.tcr_id for e in inhouse_employees if e.tcr_id}
    remote_name_by_tcr = {}
    if linked_tcr_ids:
        remote_name_by_tcr = dict(
            RemoteEmployee.objects.filter(tcr_id__in=linked_tcr_ids).values_list('tcr_id', 'name')
        )

    for employee in inhouse_employees:
        emp_shift_start, emp_shift_end = bulk_shifts[employee.id]
        approved_leave_days = bulk_leave_days[employee.id]

        employee.type = 'inhouse'
        employee.is_linked = bool(employee.tcr_id and employee.tcr_id in remote_name_by_tcr)
        employee.linked_remote_name = remote_name_by_tcr.get(employee.tcr_id)

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
        leave_days += bulk_annual_leave_non_working.get(employee.id, 0)
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

    # ── Remote employees ────────────────────────────────────────────────
    remote_records_qs = RemoteCallRecord.objects.filter(
        date__year=selected_year,
        date__month=selected_month
    ).order_by('date')

    remote_qs = RemoteEmployee.objects.prefetch_related(
        Prefetch('remotecallrecord_set', queryset=remote_records_qs, to_attr='filtered_records')
    )
    if not show_inactive:
        remote_qs = remote_qs.filter(is_active=True)
    if search_query:
        remote_qs = remote_qs.filter(name__icontains=search_query)

    remote_employees = []
    for employee in remote_qs.order_by('name'):
        if employee.tcr_id and employee.tcr_id in linked_tcr_ids:
            # Represented by the linked in-house row instead — its fingerprint calendar
            # takes priority over the (now redundant) call-data calendar.
            continue

        employee.type = 'remote'
        employee.calendar_data, stats = _compute_remote_calendar(
            employee, days_in_month, selected_year, selected_month,
            holiday_dates, current_day, special_periods=special_periods,
        )
        employee.summary = stats
        remote_employees.append(employee)

    # ── Combine ──────────────────────────────────────────────────────────
    if type_filter == 'inhouse':
        employees = inhouse_employees
    elif type_filter == 'remote':
        employees = remote_employees
    else:
        employees = inhouse_employees + remote_employees

    employees.sort(key=lambda e: e.name.lower())

    pending_requests = EarlyLeaveRequest.objects.filter(
        status='pending'
    ).select_related('employee', 'remote_employee')

    context = get_common_report_context(
        selected_month, selected_year, cal_data, holidays_qs,
        show_inactive, search_query, type_filter=type_filter,
    )
    context.update({
        'employees': employees,
        'pending_requests': pending_requests,
        'pending_count': pending_requests.count(),
        'month_name': MONTH_NAMES[selected_month],
    })
    return render(request, 'attendance/report.html', context)


@login_required
def remote_attendance_report(request):
    """Deprecated: the remote report is now merged into attendance_report. Redirect there,
    preserving the query string, so existing bookmarks/links keep working."""
    url = reverse('report')
    query = request.GET.urlencode()
    return redirect(f'{url}?{query}' if query else url)
