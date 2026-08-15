"""
Sunday Entitlement — the Django layer.

Thin on purpose. It reads an employee's joining date, annual leave and last
working date, hands them to the pure engine in
`services_sunday_entitlement.py`, and optionally persists the verdict.

All the rules live in the pure module. If you find yourself adding a business
rule here, it belongs there instead — that is the module payroll, attendance,
proration, settlement and the reports all share, and a rule that lives only on
this side is a rule the reports will not apply.
"""

import logging

from django.utils import timezone

from .services_sunday_entitlement import (
    DEFAULT_POLICY, calculate_sunday_entitlement,
)

logger = logging.getLogger('payroll')


def _leave_records(employee, employee_type, period_start, period_end):
    """Annual leave overlapping the period.

    Overlapping, not contained: leave that began last month and ends inside
    this one still makes the early Sundays of this period unavailable.
    """
    from attendance.models import AnnualLeave

    kw = ({'employee': employee} if employee_type == 'inhouse'
          else {'remote_employee': employee})
    return list(AnnualLeave.objects
                .filter(start_date__lte=period_end, end_date__gte=period_start, **kw)
                .order_by('start_date'))


def entitlement_for(employee, employee_type, year, month,
                    period_start=None, period_end=None, policy=DEFAULT_POLICY):
    """Calculate one employee's weekly-off entitlement. Writes nothing.

    The period defaults to the employee's own salary cycle, so two employees
    on different cycle start days are each measured over their own period
    rather than a shared calendar month.
    """
    if period_start is None or period_end is None:
        from .services_payroll_engine import get_pay_period
        period = get_pay_period(employee, year, month)
        period_start, period_end = period.start, period.end

    return calculate_sunday_entitlement(
        payroll_period_start=period_start,
        payroll_period_end=period_end,
        date_of_joining=getattr(employee, 'joining_date', None),
        annual_leave_records=_leave_records(employee, employee_type,
                                            period_start, period_end),
        last_working_date=getattr(employee, 'leaving_date', None),
        policy=policy,
        employee_id=employee.id,
        employee_name=employee.name,
    )


def save_entitlement(employee, employee_type, year, month, result=None,
                     policy=DEFAULT_POLICY):
    """Persist the calculation, preserving any existing HR override.

    An override is a decision a person made and signed. Recalculating must not
    quietly discard it — `system_calculated_count` is refreshed, the override
    is left exactly where it is, and the two sit side by side.
    """
    from .models import SundayEntitlementRecord

    if result is None:
        result = entitlement_for(employee, employee_type, year, month, policy=policy)

    kw = ({'employee': employee} if employee_type == 'inhouse'
          else {'remote_employee': employee})
    record, _ = SundayEntitlementRecord.objects.get_or_create(
        year=year, month=month, defaults={}, **kw)

    record.period_start = result['payroll_period_start']
    record.period_end = result['payroll_period_end']
    record.total_sundays = result['total_sundays_in_period']
    record.system_calculated_count = result['eligible_sunday_count']
    record.basis = result['basis'][:60]
    record.eligibility_start_date = result['sunday_eligibility_start_date']
    record.breakdown = result['audit_rows']
    record.save()
    return record


def apply_override(record, new_count, reason, actor):
    """Record an HR override. The calculated figure is never destroyed."""
    from django.core.exceptions import ValidationError

    if not (reason or '').strip():
        raise ValidationError('An override needs a reason.')
    record.override_count = int(new_count)
    record.override_reason = reason.strip()
    record.override_by = actor
    record.override_at = timezone.now()
    record.full_clean(exclude=['calculated_at'])
    record.save()

    from attendance.audit import log_audit
    from attendance.models import AuditLog
    log_audit(actor, AuditLog.ACTION_UPDATE, record,
              changes={'sunday_count': [str(record.system_calculated_count),
                                        str(record.override_count)]},
              note=('Sunday entitlement overridden from '
                    f'{record.system_calculated_count} to {record.override_count}: '
                    f'{reason}')[:255])
    logger.info('Sunday entitlement overridden for %s %s/%s by %s: %s -> %s',
                record.person, record.month, record.year, actor,
                record.system_calculated_count, record.override_count)
    return record


def clear_override(record, actor):
    """Revert to the calculated figure. The override history stays in AuditLog."""
    was = record.override_count
    record.override_count = None
    record.override_reason = ''
    record.override_by = ''
    record.override_at = None
    record.save()

    from attendance.audit import log_audit
    from attendance.models import AuditLog
    log_audit(actor, AuditLog.ACTION_UPDATE, record,
              changes={'sunday_count': [str(was), str(record.system_calculated_count)]},
              note='Sunday entitlement override removed — reverted to the calculated figure')
    return record


def entitlement_summary(employee, employee_type, year, month, policy=DEFAULT_POLICY):
    """What the payroll screen shows, including any stored override."""
    from .models import SundayEntitlementRecord

    result = entitlement_for(employee, employee_type, year, month, policy=policy)
    kw = ({'employee': employee} if employee_type == 'inhouse'
          else {'remote_employee': employee})
    record = SundayEntitlementRecord.objects.filter(year=year, month=month, **kw).first()

    return {
        'total_sundays': result['total_sundays_in_period'],
        'effective_date': result['sunday_eligibility_start_date'],
        'basis': result['basis'],
        'calculated_count': result['eligible_sunday_count'],
        'excluded_count': result['excluded_sunday_count'],
        'final_count': record.final_count if record else result['eligible_sunday_count'],
        'is_overridden': bool(record and record.is_overridden),
        'override_reason': record.override_reason if record else '',
        'override_by': record.override_by if record else '',
        'override_at': record.override_at if record else None,
        'record_id': record.id if record else None,
        'audit_rows': result['audit_rows'],
    }
