"""
Utility functions, decorators, and shared constants for attendance views.
"""

import datetime
import calendar
import logging
from collections import defaultdict
from datetime import timedelta, time
from functools import wraps

from django.http import JsonResponse

logger = logging.getLogger('attendance')

# Saturday working hours (fixed for all employees)
SATURDAY_SHIFT_START = time(10, 0)  # 10:00 AM
SATURDAY_SHIFT_END = time(14, 0)    # 2:00 PM (14:00)
SATURDAY_WORK_DURATION_SECONDS = 14400  # 4 hours

# Shared month data for templates
MONTH_CHOICES = [
    (1, 'January'), (2, 'February'), (3, 'March'), (4, 'April'),
    (5, 'May'), (6, 'June'), (7, 'July'), (8, 'August'),
    (9, 'September'), (10, 'October'), (11, 'November'), (12, 'December')
]
MONTH_NAMES = ['', 'January', 'February', 'March', 'April', 'May', 'June',
               'July', 'August', 'September', 'October', 'November', 'December']
YEAR_RANGE = range(2020, 2036)
WEEKDAY_HEADERS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']


def superuser_required(user):
    """Check if user is a superuser. Used with @user_passes_test."""
    return user.is_superuser


def it_admin_required(user):
    """Check if user is a superuser flagged as IT Admin. Used with @user_passes_test.

    Gates the custom User Management page — separate from superuser_required,
    which only gates general admin sections.
    """
    if not user.is_authenticated or not user.is_superuser:
        return False
    return getattr(user, 'profile', None) is not None and user.profile.is_it_admin


# Sidebar pages that can be individually granted/revoked per user via the
# User Management page. (key, label, group) — group is only used to cluster
# checkboxes in that page's UI; enforcement only cares about the key.
NAV_SECTIONS = [
    # (key, label, group) — group clusters checkboxes in User Management UI
    ('employees',       'Employees',        'People'),
    ('leave_requests',  'Leave Requests',   'People'),
    ('annual_leave',    'Annual Leave',     'People'),
    ('special_shifts',  'Special Shifts',   'Scheduling'),
    ('on_duty_requests','On-Duty Requests', 'Administration'),
    ('payroll',         'Payroll',          'Compensation'),
    ('banks',           'Banks',            'Administration'),
    ('upload',          'Upload Data',      'Data Upload'),
    ('management',      'Management Dashboard', 'Administration'),   # Phase 12
    ('audit_log',       'Audit Log',        'Administration'),        # Phase 13
]
NAV_SECTION_KEYS = {key for key, _, _ in NAV_SECTIONS}


def has_section_access(user, section_key):
    """Check if user may access a given sidebar page.

    Superusers have full access by default; access is only narrowed once
    itadmin explicitly turns on sections_restricted for that account.
    """
    if not user.is_authenticated or not user.is_superuser:
        return False
    profile = getattr(user, 'profile', None)
    if not profile or not profile.sections_restricted:
        return True
    return section_key in (profile.allowed_sections or [])


def section_required(section_key):
    """Build a @user_passes_test predicate gating a specific sidebar page."""
    def check(user):
        return has_section_access(user, section_key)
    return check


def get_user_nav_sections(user):
    """Set of section keys the user may access. Used to drive sidebar visibility."""
    if not user.is_authenticated or not user.is_superuser:
        return set()
    profile = getattr(user, 'profile', None)
    if not profile or not profile.sections_restricted:
        return set(NAV_SECTION_KEYS)
    return set(profile.allowed_sections or []) & NAV_SECTION_KEYS


def require_post_json(view_func):
    """Decorator that requires POST method and returns JSON for errors."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if request.method != 'POST':
            return JsonResponse({'success': False, 'error': 'POST required'}, status=405)
        return view_func(request, *args, **kwargs)
    return wrapper


def parse_duration(duration_str):
    """Parse duration string like 'HH:MM:SS' to timedelta."""
    if not duration_str or str(duration_str).strip() == '':
        return timedelta(0)
    try:
        parts = str(duration_str).split(':')
        if len(parts) == 3:
            hours, minutes, seconds = map(int, parts)
            return timedelta(hours=hours, minutes=minutes, seconds=seconds)
        elif len(parts) == 2:
            minutes, seconds = map(int, parts)
            return timedelta(minutes=minutes, seconds=seconds)
    except (ValueError, AttributeError):
        logger.warning("Failed to parse duration string: %s", duration_str)
    return timedelta(0)


def get_saturday_shift():
    """
    Returns fixed Saturday shift timings (always 10:00 AM - 2:00 PM for all employees).
    Returns: (shift_start, shift_end) as time objects
    """
    return SATURDAY_SHIFT_START, SATURDAY_SHIFT_END


def get_active_special_periods_for_month(month_start, month_end):
    """
    Return all SpecialShiftPeriod records that overlap with the given month.
    Cached as a list so callers can do in-memory lookups per day.
    """
    from attendance.models import SpecialShiftPeriod
    return list(SpecialShiftPeriod.objects.filter(
        start_date__lte=month_end,
        end_date__gte=month_start,
    ).order_by('start_date'))


def get_remote_thresholds_from_period(period):
    """
    Extract remote call-minute thresholds from a SpecialShiftPeriod.
    Returns a dict suitable for RemoteCallRecord.calculate_attendance_status(),
    or None if the period has no remote thresholds set.
    """
    thresholds = {}
    if period.remote_weekday_half_day_mins is not None:
        thresholds['weekday'] = (period.remote_weekday_half_day_mins, period.remote_weekday_present_mins)
    if period.remote_friday_half_day_mins is not None:
        thresholds['friday'] = (period.remote_friday_half_day_mins, period.remote_friday_present_mins)
    if period.remote_saturday_half_day_mins is not None:
        thresholds['saturday'] = (period.remote_saturday_half_day_mins, period.remote_saturday_present_mins)
    return thresholds or None


# Remote Sales:Performance payroll switched from the old present/half/absent
# attendance calculation to the talktime-proportional daily-pay model ("Method
# 2", see compute_sales_performance_v2_days / payroll._get_sales_performance_test_row)
# starting with this salary month. Months before this keep using the original
# calculation so already-computed (and especially already-paid/frozen) payroll
# is never recalculated with a different formula, and the calendar view stays
# consistent with whichever formula actually determined pay for that month.
SALES_PERFORMANCE_V2_START = (2026, 7)


def remote_employee_uses_performance_v2(employee, year, month):
    """True if a remote employee's attendance for (year, month) is governed by
    the Sales:Performance "Method 2" pay model rather than plain call-duration
    thresholds — i.e. the same condition payroll._get_sales_payroll_row uses to
    route into _get_sales_performance_test_row. Used by both payroll and the
    attendance calendar/report/portal views so day classification never drifts
    between the two.
    """
    return (
        not employee.is_fixed_salary
        and getattr(employee, 'payroll_type', 'attendance') == 'attendance'
        and bool(employee.salary)
        and (year, month) >= SALES_PERFORMANCE_V2_START
    )


def compute_sales_performance_v2_days(employee, period_start, period_end, holiday_dates):
    """Day-by-day Method 2 classification for a remote Sales:Performance
    employee across [period_start, period_end].

    Shared by payroll's pay calculation (_get_sales_performance_test_row) and
    the attendance calendar/report/portal views, so both always agree on which
    regime and classification governs a given day. See
    payroll.views._get_sales_performance_test_row for the full rule spec
    (regime routing, grace-band gates, new-joiner-month handling).

    Returns a dict with:
      'days': {date: {'regime': 'non_working'|'friday_saturday'|'new_joiner'|'standard',
                       'classification': 'non_working'|'full'|'half'|'proportional'|'leave',
                       'tvm': int (talk minutes),
                       'grace_denial_reason': None or one of the three cap-exhaustion reasons}}
      plus aggregate counts: 'leave_days', 'half_days', 'proportional_days',
      'full_days', 'non_working_days', 'new_joiner_days', 'grace_denials'.
    """
    from attendance.models import RemoteCallRecord, AnnualLeave

    NEW_JOINER_FULL_DAY_THRESHOLD = 60
    STANDARD_FULL_DAY_THRESHOLD = 90
    GRACE_BAND_MIN = 55
    HALF_DAY_THRESHOLD = 45
    FRI_SAT_THRESHOLD = 30
    MONTHLY_GRACE_CAP = 7
    WEEKLY_GRACE_CAP = 2
    CONSECUTIVE_GRACE_CAP = 2

    call_records = {
        r.date: r for r in RemoteCallRecord.objects.filter(
            employee=employee, date__gte=period_start, date__lte=period_end,
        ).only('date', 'total_talk_duration')
    }

    # Sundays/holidays that fall inside an AnnualLeave span are normally paid
    # at the full daily wage below. During annual leave they should instead
    # follow the leave's own pay rate — 0% for unpaid leave, salary_percentage%
    # for paid leave — otherwise an unpaid leave still gets its Sundays paid
    # in full for free. Maps date -> salary_pct for every day covered by an
    # AnnualLeave span (only consulted for non-working days below).
    annual_leave_pct_by_date = {}
    for al in AnnualLeave.objects.filter(
        remote_employee=employee, start_date__lte=period_end, end_date__gte=period_start,
    ):
        salary_pct = float(al.salary_percentage) if al.is_paid else 0.0
        curr = max(al.start_date, period_start)
        end = min(al.end_date, period_end)
        while curr <= end:
            annual_leave_pct_by_date[curr] = salary_pct
            curr += datetime.timedelta(days=1)

    days = {}
    leave_days = half_days = proportional_days = full_days = 0
    non_working_days = new_joiner_days = 0
    grace_denials = defaultdict(int)
    monthly_used = defaultdict(int)
    weekly_used = defaultdict(int)
    consecutive = 0

    d = period_start
    while d <= period_end:
        rec = call_records.get(d)
        tvm = int(rec.total_talk_duration.total_seconds() // 60) if (rec and rec.total_talk_duration) else 0

        if d.weekday() == 6 or d in holiday_dates:
            non_working_days += 1
            days[d] = {
                'regime': 'non_working', 'classification': 'non_working', 'tvm': tvm,
                'grace_denial_reason': None,
                'annual_leave_salary_pct': annual_leave_pct_by_date.get(d),
            }
            d += datetime.timedelta(days=1)
            continue

        if d.weekday() in (4, 5):  # Friday, Saturday -> binary 30-min threshold
            if tvm >= FRI_SAT_THRESHOLD:
                full_days += 1
                days[d] = {'regime': 'friday_saturday', 'classification': 'full', 'tvm': tvm, 'grace_denial_reason': None}
            else:
                leave_days += 1
                days[d] = {'regime': 'friday_saturday', 'classification': 'leave', 'tvm': tvm, 'grace_denial_reason': None}
            d += datetime.timedelta(days=1)
            continue

        # Monday-Thursday
        # New-joiner window is a rolling 30 days from joining_date (day 0
        # through day 29 inclusive), not the calendar month of joining_date —
        # otherwise someone who joins near month-end (e.g. the 29th) would get
        # only a couple of lenient days before standard thresholds kick in.
        is_new_joiner_month = bool(employee.joining_date) and 0 <= (d - employee.joining_date).days < 30

        if is_new_joiner_month:
            new_joiner_days += 1
            if tvm >= NEW_JOINER_FULL_DAY_THRESHOLD:
                full_days += 1
                classification = 'full'
            elif tvm >= HALF_DAY_THRESHOLD:
                half_days += 1
                classification = 'half'
            else:
                leave_days += 1
                classification = 'leave'
            days[d] = {'regime': 'new_joiner', 'classification': classification, 'tvm': tvm, 'grace_denial_reason': None}
            d += datetime.timedelta(days=1)
            continue

        # Standard regime
        if tvm >= STANDARD_FULL_DAY_THRESHOLD:
            full_days += 1
            days[d] = {'regime': 'standard', 'classification': 'full', 'tvm': tvm, 'grace_denial_reason': None}
            consecutive = 0
        elif tvm >= GRACE_BAND_MIN:  # 55-89: grace-band, subject to the three gates
            month_key = (d.year, d.month)
            week_key = d - datetime.timedelta(days=d.weekday())
            denial_reason = None
            if monthly_used[month_key] >= MONTHLY_GRACE_CAP:
                denial_reason = 'MONTHLY_CAP_EXHAUSTED'
            elif weekly_used[week_key] >= WEEKLY_GRACE_CAP:
                denial_reason = 'WEEKLY_CAP_REACHED'
            elif consecutive >= CONSECUTIVE_GRACE_CAP:
                denial_reason = 'CONSECUTIVE_CAP_REACHED'

            if denial_reason:
                half_days += 1
                grace_denials[denial_reason] += 1
                days[d] = {'regime': 'standard', 'classification': 'half', 'tvm': tvm, 'grace_denial_reason': denial_reason}
                consecutive = 0
            else:
                proportional_days += 1
                monthly_used[month_key] += 1
                weekly_used[week_key] += 1
                consecutive += 1
                days[d] = {'regime': 'standard', 'classification': 'proportional', 'tvm': tvm, 'grace_denial_reason': None}
        elif tvm >= HALF_DAY_THRESHOLD:  # 45-54
            half_days += 1
            days[d] = {'regime': 'standard', 'classification': 'half', 'tvm': tvm, 'grace_denial_reason': None}
            consecutive = 0
        else:
            leave_days += 1
            days[d] = {'regime': 'standard', 'classification': 'leave', 'tvm': tvm, 'grace_denial_reason': None}
            consecutive = 0

        d += datetime.timedelta(days=1)

    return {
        'days': days,
        'leave_days': leave_days, 'half_days': half_days,
        'proportional_days': proportional_days, 'full_days': full_days,
        'non_working_days': non_working_days, 'new_joiner_days': new_joiner_days,
        'grace_denials': dict(grace_denials),
    }


def get_employee_shift_for_date(employee, target_date):
    """
    Get employee's shift timings for a specific date using 3-tier lookup strategy.

    Priority:
    1. ShiftHistory: Most recent shift with effective_from <= target_date
    2. Employee direct fields: employee.shift_start and employee.shift_end
    3. System defaults: 10:00-19:00

    Note: This applies to Monday-Friday only. Saturday always uses 10:00-14:00.
    """
    from attendance.models import ShiftHistory

    # Tier 1: Check ShiftHistory
    applicable_shift = ShiftHistory.objects.filter(
        employee=employee,
        effective_from__lte=target_date
    ).order_by('-effective_from').first()

    if applicable_shift:
        return applicable_shift.shift_start, applicable_shift.shift_end

    # Tier 2: Check Employee direct fields
    if employee.shift_start and employee.shift_end:
        return employee.shift_start, employee.shift_end

    # Tier 3: System defaults
    return time(10, 0), time(19, 0)


def get_selected_month_year(request):
    """Extract and validate month/year from request GET params. Returns (month, year).

    Falls back to the last month/year the user selected anywhere in the app
    (stored in session) instead of always defaulting to the current month, so
    a choice made on one page (e.g. the report) carries over to others (e.g.
    payroll) instead of silently resetting. Whenever month/year are given
    explicitly, they become the new "current selection" for the session.
    """
    now = datetime.datetime.now()
    session = getattr(request, 'session', None)
    default_month = (session and session.get('selected_month')) or now.month
    default_year = (session and session.get('selected_year')) or now.year
    explicit = 'month' in request.GET or 'year' in request.GET

    try:
        month = int(request.GET.get('month', default_month))
        year = int(request.GET.get('year', default_year))
        if not (1 <= month <= 12):
            month = now.month
        if not (2000 <= year <= 2099):
            year = now.year
    except (ValueError, TypeError):
        month = now.month
        year = now.year

    if explicit and session is not None:
        session['selected_month'] = month
        session['selected_year'] = year

    return month, year


def build_calendar_grid(year, month):
    """
    Build calendar grid data for a given month.

    Returns dict with: days_in_month, calendar_days (list with None padding),
    first_weekday_sunday, month_start, month_end.
    """
    first_weekday, days_in_month = calendar.monthrange(year, month)
    first_weekday_sunday = (first_weekday + 1) % 7

    calendar_days = [None] * first_weekday_sunday + list(range(1, days_in_month + 1))
    while len(calendar_days) % 7 != 0:
        calendar_days.append(None)

    month_start = datetime.date(year, month, 1)
    month_end = datetime.date(year, month, days_in_month)

    return {
        'days_in_month': days_in_month,
        'calendar_days': calendar_days,
        'month_start': month_start,
        'month_end': month_end,
    }


def get_holiday_data(month_start, month_end):
    """
    Get holiday dates and names for a date range.
    Returns: (holiday_dates: set, holiday_names: dict, holidays_queryset)
    """
    from attendance.models import Holiday

    holidays_qs = Holiday.objects.filter(date__gte=month_start, date__lte=month_end)
    holiday_dates = set(h.date for h in holidays_qs)
    holiday_names = {h.date: h.name for h in holidays_qs}
    return holiday_dates, holiday_names, holidays_qs


def count_holidays_in_range(year, month, end_day, holiday_dates):
    """
    Count Sundays and custom holidays from day 1 to end_day (inclusive).

    Returns: (sundays_count, custom_holidays_count, total_holidays, expected_working_days)
    """
    sundays = 0
    custom_holidays = 0
    for day in range(1, end_day + 1):
        date = datetime.date(year, month, day)
        if date.weekday() == 6:
            sundays += 1
        elif date in holiday_dates:
            custom_holidays += 1

    total = sundays + custom_holidays
    expected_working = end_day - total
    return sundays, custom_holidays, total, expected_working


def get_approved_leave_days(employee, month_start, month_end):
    """
    Get set of day numbers that have approved leave for an employee in a month.
    Returns: set of day integers (1-31)

    Sandwich rule: if the Saturday before AND the Monday after a Sunday are
    both approved leave (whether as one span or two separate requests), the
    Sunday in between is auto-added as leave too. The 2-day buffer on the
    query lets this bridge work across month boundaries.
    """
    from attendance.models import LeaveRequest

    query_start = month_start - datetime.timedelta(days=2)
    query_end = month_end + datetime.timedelta(days=2)
    approved_leaves = LeaveRequest.objects.filter(
        employee=employee,
        status='approved',
        start_date__lte=query_end,
        end_date__gte=query_start,
    )

    leave_dates = set()
    for leave in approved_leaves:
        start = max(leave.start_date, query_start)
        end = min(leave.end_date, query_end)
        curr = start
        while curr <= end:
            leave_dates.add(curr)
            curr += datetime.timedelta(days=1)

    current = month_start
    while current <= month_end:
        if current.weekday() == 6:
            sat = current - datetime.timedelta(days=1)
            mon = current + datetime.timedelta(days=1)
            if sat in leave_dates and mon in leave_dates:
                leave_dates.add(current)
        current += datetime.timedelta(days=1)

    return {d.day for d in leave_dates if month_start <= d <= month_end}


def get_bridge_sunday_days(employee, month_start, month_end):
    """
    Return day numbers of Sundays in [month_start, month_end] that are
    sandwiched between an approved-leave Saturday and an approved-leave
    Monday for this employee. These Sundays are treated as unpaid leave
    in payroll (one daily_rate deducted per day) regardless of whether the
    surrounding leaves are themselves paid or unpaid.

    Bridges any combination of approved LeaveRequest (in-house only) and
    AnnualLeave (in-house or remote). Sundays that fall inside one
    continuous Sat-Sun-Mon leave span are excluded — those are already
    handled by their own leave record.

    A 2-day buffer on the queries lets the bridge work across month
    boundaries.
    """
    from attendance.models import LeaveRequest, AnnualLeave, RemoteEmployee

    query_start = month_start - datetime.timedelta(days=2)
    query_end = month_end + datetime.timedelta(days=2)

    is_remote = isinstance(employee, RemoteEmployee)

    lr_spans = []   # from LeaveRequest (Sundays NOT added to leave_days)
    al_spans = []   # from AnnualLeave  (Sundays ARE added to leave_days by recalculate_summaries)

    if not is_remote:
        for lr in LeaveRequest.objects.filter(
            employee=employee,
            status='approved',
            start_date__lte=query_end,
            end_date__gte=query_start,
        ):
            lr_spans.append((lr.start_date, lr.end_date))

    al_qs = AnnualLeave.objects.filter(
        start_date__lte=query_end,
        end_date__gte=query_start,
    )
    if is_remote:
        al_qs = al_qs.filter(remote_employee=employee)
    else:
        al_qs = al_qs.filter(employee=employee)
    for al in al_qs:
        al_spans.append((al.start_date, al.end_date))

    leave_dates = set()
    for start_date, end_date in lr_spans + al_spans:
        start = max(start_date, query_start)
        end = min(end_date, query_end)
        curr = start
        while curr <= end:
            leave_dates.add(curr)
            curr += datetime.timedelta(days=1)

    # All Sundays within any AnnualLeave span are excluded from bridge.
    # annual_leave_extra_deduction in the payroll calculation already charges
    # (100 − salary_pct)% for non-working days within AnnualLeave.  Counting
    # them again as bridge Sundays would be a double deduction.
    # Sundays inside a LeaveRequest span are NOT excluded — they are not
    # charged by any other mechanism and must be counted as bridge days.
    al_sunday_set = set()
    for start_date, end_date in al_spans:
        curr = max(start_date, query_start)
        end = min(end_date, query_end)
        while curr <= end:
            if curr.weekday() == 6:
                al_sunday_set.add(curr)
            curr += datetime.timedelta(days=1)

    bridge = set()
    current = month_start
    while current <= month_end:
        if current.weekday() == 6 and current not in al_sunday_set:
            sat = current - datetime.timedelta(days=1)
            mon = current + datetime.timedelta(days=1)
            if sat in leave_dates and mon in leave_dates:
                bridge.add(current.day)
        current += datetime.timedelta(days=1)
    return bridge


def get_common_report_context(month, year, cal_data, holidays_qs, show_inactive, search_query,
                               type_filter=''):
    """Build the common template context used by report views."""
    today = datetime.date.today()
    if year == today.year and month == today.month:
        current_day = today.day
    elif (year < today.year) or (year == today.year and month < today.month):
        current_day = 32  # Past month: show all days
    else:
        current_day = 0  # Future month

    return {
        'selected_month': month,
        'selected_year': year,
        'months': MONTH_CHOICES,
        'years': YEAR_RANGE,
        'calendar_days': cal_data['calendar_days'],
        'weekdays': WEEKDAY_HEADERS,
        'days_in_month': cal_data['days_in_month'],
        'show_inactive': show_inactive,
        'search_query': search_query,
        'type_filter': type_filter,
        'holiday_days': [h.date.day for h in holidays_qs],
        'holiday_names': {h.date.day: h.name for h in holidays_qs},
        'current_day': current_day,
    }


def get_bulk_employee_shifts(employees, target_date):
    """
    Fetch shift timings for a list of in-house employees in a single DB query.

    Uses the same 3-tier priority as get_employee_shift_for_date:
    1. Most recent ShiftHistory with effective_from <= target_date
    2. Employee direct fields (shift_start / shift_end)
    3. System default (10:00–19:00)

    Returns {employee_id: (shift_start, shift_end)}.
    """
    from attendance.models import ShiftHistory

    employee_ids = [e.id for e in employees]
    result = {}

    # Single query: all applicable shift-history rows, newest-first per employee
    histories = (
        ShiftHistory.objects
        .filter(employee_id__in=employee_ids, effective_from__lte=target_date)
        .order_by('employee_id', '-effective_from')
        .values('employee_id', 'shift_start', 'shift_end')
    )

    seen = set()
    for h in histories:
        emp_id = h['employee_id']
        if emp_id not in seen:
            result[emp_id] = (h['shift_start'], h['shift_end'])
            seen.add(emp_id)

    # Fall back to employee fields or system default for employees with no history
    for emp in employees:
        if emp.id not in result:
            if emp.shift_start and emp.shift_end:
                result[emp.id] = (emp.shift_start, emp.shift_end)
            else:
                result[emp.id] = (time(10, 0), time(19, 0))

    return result


def get_bulk_approved_leave_days(employees, month_start, month_end):
    """
    Fetch approved leave day-sets for a list of in-house employees in a single DB query.

    Returns {employee_id: set_of_day_ints (1–31)}.
    """
    from attendance.models import LeaveRequest

    employee_ids = [e.id for e in employees]
    result = {e.id: set() for e in employees}

    leaves = LeaveRequest.objects.filter(
        employee_id__in=employee_ids,
        status='approved',
        start_date__lte=month_end,
        end_date__gte=month_start,
    ).values('employee_id', 'start_date', 'end_date')

    for leave in leaves:
        start = max(leave['start_date'], month_start)
        end = min(leave['end_date'], month_end)
        curr = start
        while curr <= end:
            result[leave['employee_id']].add(curr.day)
            curr += datetime.timedelta(days=1)

    return result


def get_bulk_annual_leave_non_working_days(employees, month_start, month_end, holiday_dates):
    """
    Count Sundays and custom holidays that fall inside AnnualLeave periods for each
    in-house employee. Returns {employee_id: int}.

    These days normally appear as 'holiday' in the calendar, but when an employee is
    on AnnualLeave they should be counted toward the leave total so the displayed
    leave_days matches the full calendar duration of the leave period.
    """
    from attendance.models import AnnualLeave

    employee_ids = [e.id for e in employees]
    result = {e.id: 0 for e in employees}

    annual_leaves = AnnualLeave.objects.filter(
        employee_id__in=employee_ids,
        start_date__lte=month_end,
        end_date__gte=month_start,
    )

    for al in annual_leaves:
        overlap_start = max(al.start_date, month_start)
        overlap_end = min(al.end_date, month_end)
        curr = overlap_start
        while curr <= overlap_end:
            if curr.weekday() == 6 or curr in holiday_dates:
                result[al.employee_id] += 1
            curr += timedelta(days=1)

    return result
