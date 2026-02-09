"""
Utility functions and decorators shared across views.
"""

from datetime import timedelta, time


# Saturday working hours (fixed for all employees)
SATURDAY_SHIFT_START = time(10, 0)  # 10:00 AM
SATURDAY_SHIFT_END = time(14, 0)    # 2:00 PM (14:00)
SATURDAY_WORK_DURATION_SECONDS = 14400  # 4 hours


def superuser_required(user):
    """Check if user is a superuser."""
    return user.is_superuser


def parse_duration(duration_str):
    """Parse duration string like 'HH:MM:SS' to timedelta."""
    if not duration_str or duration_str == '':
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
        pass
    return timedelta(0)


def get_saturday_shift():
    """
    Returns fixed Saturday shift timings (always 10:00 AM - 2:00 PM for all employees).

    Returns: (shift_start, shift_end) as time objects
    """
    return SATURDAY_SHIFT_START, SATURDAY_SHIFT_END


def get_employee_shift_for_date(employee, target_date):
    """
    Get employee's shift timings for a specific date using 3-tier lookup strategy.

    This function checks shift timings in the following priority:
    1. ShiftHistory: Most recent shift with effective_from <= target_date
    2. Employee direct fields: employee.shift_start and employee.shift_end
    3. System defaults: 10:00-19:00

    Note: This applies to Monday-Friday only. Saturday always uses 10:00-14:00
    regardless of shift history (use get_saturday_shift() instead).

    Args:
        employee: Employee object
        target_date: date object for which to get shift timings

    Returns: (shift_start, shift_end) as time objects
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
