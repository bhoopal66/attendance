"""Opening and closing employee assignments — the one way the history moves.

There is exactly one supported way to change where somebody sits:
`open_assignment()`. It closes the arrangement in force and opens the next one,
in a single transaction. Nothing else should write to `EmployeeAssignment`.

WHY NOT JUST EDIT THE ROW
-------------------------
Because §80 is right. `Employee → edit → change department → save` destroys the
answer to "who did they report to in March", and that question is asked after
somebody disputes an appraisal, not before. A change is a new row; the old row
closes the day before the new one starts and is never touched again.

WHY ONE TRANSACTION
-------------------
A promotion moves designation, grade, manager and often department at once
(§88). If closing the old row succeeded and opening the new one failed, the
employee would be left with no current assignment at all — worse than the
overwrite this replaces. Either both happen or neither does.
"""
import datetime
import logging

from django.core.exceptions import ValidationError
from django.db import transaction

logger = logging.getLogger('attendance')

# Fields carried forward when a change does not mention them. A promotion that
# only names a new designation must not silently blank the department.
CARRIED = (
    'company', 'department', 'team', 'location', 'designation', 'grade',
    'job_level', 'cost_centre', 'reporting_manager', 'functional_manager',
)


def _person_filter(employee):
    from attendance.models import Employee
    if isinstance(employee, Employee):
        return {'employee': employee, 'remote_employee': None}
    return {'employee': None, 'remote_employee': employee}


def current_assignment(employee, as_of=None):
    """The assignment in force, on a date. None if there is none.

    `as_of` matters: asking "who was their manager" about a past month must not
    return today's answer. That is the entire point of the table.
    """
    from attendance.models import EmployeeAssignment
    qs = EmployeeAssignment.objects.filter(**_person_filter(employee))
    if as_of is None:
        return qs.filter(is_current=True).order_by('-effective_from').first()
    from django.db.models import Q
    return (qs.filter(effective_from__lte=as_of)
              .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=as_of))
              .order_by('-effective_from').first())


def history(employee):
    """Every assignment, newest first."""
    from attendance.models import EmployeeAssignment
    return EmployeeAssignment.objects.filter(**_person_filter(employee)).order_by(
        '-effective_from', '-id')


@transaction.atomic
def open_assignment(employee, effective_from, change_type, changes,
                    reason='', actor='', approved_by='', approved_at=None):
    """Close what is in force and open the next arrangement. Returns the new row.

    `changes` names only what actually changes; everything else is carried
    forward from the current assignment.

    Refuses to backdate on top of a closed period. Rewriting a period that has
    already been paid against is not an edit, it is a correction, and it needs
    somebody to look at the payroll consequences rather than a function call.
    """
    from attendance.models import EmployeeAssignment

    if isinstance(effective_from, datetime.datetime):
        effective_from = effective_from.date()

    existing = current_assignment(employee)

    if existing and effective_from <= existing.effective_from:
        raise ValidationError(
            'A new assignment must start after the current one began (%s). '
            'To correct the current row, edit it as a correction instead.'
            % existing.effective_from)

    closed_over = history(employee).filter(effective_to__isnull=False,
                                           effective_to__gte=effective_from).first()
    if closed_over:
        raise ValidationError(
            'Backdating to %s would land inside a closed period (%s to %s). '
            'That period may already have been paid against — correct it '
            'deliberately rather than through a new assignment.'
            % (effective_from, closed_over.effective_from, closed_over.effective_to))

    data = {}
    for field in CARRIED:
        data[field] = getattr(existing, field) if existing else None
        if data[field] is None and field not in ('company', 'reporting_manager',
                                                 'functional_manager'):
            data[field] = ''
    data.update(changes or {})

    new = EmployeeAssignment(
        effective_from=effective_from, effective_to=None, is_current=True,
        change_type=change_type, reason=reason or '',
        created_by=actor or '', approved_by=approved_by or '',
        approved_at=approved_at, **_person_filter(employee), **data)

    if existing:
        existing.effective_to = effective_from - datetime.timedelta(days=1)
        existing.is_current = False
        existing.full_clean(exclude=['employee', 'remote_employee'])
        existing.save(update_fields=['effective_to', 'is_current', 'updated_at'])

    new.full_clean(exclude=['employee', 'remote_employee'])
    new.save()

    try:
        from attendance.audit import log_audit
        log_audit(actor=actor or 'system', action='assignment.open',
                  instance=new,
                  note='%s effective %s' % (change_type, effective_from))
    except Exception:                                           # noqa: BLE001
        # Audit must never be the reason a payroll change fails.
        logger.exception('assignment audit failed for %s', employee)

    return new
