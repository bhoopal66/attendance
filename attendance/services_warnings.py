"""Disciplinary warnings — issue, withdraw, acknowledge.

Every state change is audited: a warning is an action a manager took against
a specific person, and "who issued this, when, and was it ever withdrawn"
must be answerable later, not just visible in whatever the current row says.
"""
import logging

from django.core.exceptions import ValidationError
from django.utils import timezone

logger = logging.getLogger('attendance')


def _person_filter(person):
    from attendance.models import Employee
    if isinstance(person, Employee):
        return {'employee': person, 'remote_employee': None}
    return {'employee': None, 'remote_employee': person}


def issue_warning(person, actor='', **fields):
    """Create a new warning row and audit it."""
    from attendance.models import AuditLog, EmployeeWarning
    from attendance.audit import log_audit

    warning = EmployeeWarning(**_person_filter(person), created_by=actor or '', **fields)
    warning.full_clean(exclude=['employee', 'remote_employee', 'document'])
    warning.save()

    log_audit(actor, AuditLog.ACTION_CREATE, warning,
              note=f'Warning issued for {person}: {warning.get_severity_display()}'[:255])
    return warning


def withdraw_warning(warning, actor, reason):
    """Mark a warning withdrawn. The row is kept, not deleted."""
    from attendance.models import AuditLog
    from attendance.audit import log_audit

    if not (reason or '').strip():
        raise ValidationError('Withdrawing a warning needs a reason.')
    before_status = warning.status
    warning.status = 'withdrawn'
    warning.notes = (warning.notes + f'\nWithdrawn: {reason}').strip()
    warning.save(update_fields=['status', 'notes', 'updated_at'])

    log_audit(actor, AuditLog.ACTION_UPDATE, warning,
              changes={'status': [before_status, 'withdrawn']},
              note=f'Warning withdrawn: {reason}'[:255])
    return warning


def acknowledge(warning, actor):
    """Record that the employee acknowledged the warning."""
    from attendance.models import AuditLog
    from attendance.audit import log_audit

    warning.acknowledged_by_employee = True
    warning.acknowledged_at = timezone.now()
    warning.save(update_fields=['acknowledged_by_employee', 'acknowledged_at', 'updated_at'])

    log_audit(actor, AuditLog.ACTION_UPDATE, warning, note='Warning acknowledged by employee')
    return warning


def active_warnings(person):
    from attendance.models import EmployeeWarning
    return EmployeeWarning.objects.filter(**_person_filter(person), status='active')


def warning_history(person):
    from attendance.models import EmployeeWarning
    return EmployeeWarning.objects.filter(**_person_filter(person)).order_by('-issued_date', '-id')
