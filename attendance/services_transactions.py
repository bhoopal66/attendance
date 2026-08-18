"""The HR transactions — §80. One way in for each kind of change.

    promote()        transfer()        change_manager()
    revise_salary()  change_status()

Each one FREEZES what is proposed, opens the configured approval chain, and
returns the request. Nothing happens to the employee until the last approver
says yes; then the registered applier writes the history and the timeline.

WHAT THIS REPLACES
------------------
`Employee → edit → change salary → Save`. The spec names that as the
anti-pattern (§80) and it is: it destroys the previous value, records no
reason, asks nobody, and leaves the payroll engine to discover the change on
its own. These functions are the alternative, and the edit-in-place screens
should eventually call them instead of writing fields.

WHY A SALARY ROW IS NOT CREATED AT SUBMIT TIME
----------------------------------------------
`get_effective_salary_structure()` selects on `status='approved'`. A proposed
revision saved as a real row would be one status flag away from paying somebody
an amount nobody approved. The numbers live in the approval payload — which is
already the frozen snapshot — and a SalaryStructure row is only created when
the last approver has signed. A rejected revision therefore leaves no salary
row at all, which is correct: it never existed.

WHY PREVIOUS SALARY ROWS ARE NOT SUPERSEDED
-------------------------------------------
The obvious move on approving a revision is to mark the old row 'superseded'.
It would be a bug. `get_effective_salary_structure(employee, some_past_date)`
filters on `status='approved'` AND `effective_from <= date` — superseding the
old row makes every historical payslip find NO structure and refuse to print.
Old rows stay approved; the latest effective_from wins. The history is the
sequence of rows, not a flag on them.
"""
import datetime
import logging

from django.core.exceptions import ValidationError
from django.utils import timezone

from attendance import services_approvals as approvals
from attendance import services_timeline as timeline

logger = logging.getLogger('attendance')

REQUEST_SALARY_REVISION = 'salary_revision'
REQUEST_STATUS_CHANGE = 'status_change'

SALARY_COMPONENTS = ('basic', 'housing', 'transport', 'phone', 'other_allowance')


def _as_date(value):
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, str):
        return datetime.date.fromisoformat(value)
    return value


# ---------------------------------------------------------------- assignments

def _assignment_change(employee, effective_from, change_type, changes, summary,
                       reason='', actor=''):
    from attendance.models import Employee

    payload = dict(changes or {})
    payload['change_type'] = change_type
    # Managers must survive JSON, so they travel as ids and are resolved when
    # the change is applied — a name would go stale, an object will not encode.
    for key in ('reporting_manager', 'functional_manager'):
        if key in payload and isinstance(payload[key], Employee):
            payload[key] = payload[key].pk
    return approvals.submit(
        approvals.REQUEST_ASSIGNMENT_CHANGE, employee, payload, actor=actor,
        summary=summary, effective_date=_as_date(effective_from), reason=reason)


def promote(employee, effective_from, designation=None, grade=None,
            department=None, reporting_manager=None, reason='', actor=''):
    """Designation and/or grade up, effective on a date, subject to approval."""
    from attendance.models import EmployeeAssignment
    changes = {}
    if designation is not None:
        changes['designation'] = designation
    if grade is not None:
        changes['grade'] = grade
    if department is not None:
        changes['department'] = department
    if reporting_manager is not None:
        changes['reporting_manager'] = reporting_manager
    if not changes:
        raise ValidationError('A promotion has to change something.')
    bits = ' · '.join(str(v) for v in (designation, grade) if v)
    return _assignment_change(employee, effective_from,
                              EmployeeAssignment.CHANGE_PROMOTION, changes,
                              summary=f'Promotion: {bits}' if bits else 'Promotion',
                              reason=reason, actor=actor)


def transfer(employee, effective_from, department=None, team=None, location=None,
             cost_centre=None, company=None, reporting_manager=None,
             reason='', actor=''):
    """Move somebody — department, team, location, cost centre, or entity."""
    from attendance.models import EmployeeAssignment
    changes = {}
    for key, value in (('department', department), ('team', team),
                       ('location', location), ('cost_centre', cost_centre),
                       ('reporting_manager', reporting_manager)):
        if value is not None:
            changes[key] = value
    if company is not None:
        changes['company'] = company.pk if hasattr(company, 'pk') else company
    if not changes:
        raise ValidationError('A transfer has to move something.')
    where = ' · '.join(str(v) for v in (department, team, location) if v)
    return _assignment_change(employee, effective_from,
                              EmployeeAssignment.CHANGE_TRANSFER, changes,
                              summary=f'Transfer: {where}' if where else 'Transfer',
                              reason=reason, actor=actor)


def change_manager(employee, effective_from, new_manager, reason='', actor=''):
    """The change that today destroys the previous manager entirely."""
    from attendance.models import EmployeeAssignment
    if new_manager is None:
        raise ValidationError('A manager change needs a manager.')
    return _assignment_change(
        employee, effective_from, EmployeeAssignment.CHANGE_MANAGER,
        {'reporting_manager': new_manager},
        summary=f'Reports to {getattr(new_manager, "name", new_manager)}',
        reason=reason, actor=actor)


# ------------------------------------------------------------------- salary

def revise_salary(employee, effective_from, components, revision_type='',
                  reason='', actor=''):
    """Propose a new salary structure. Creates NO salary row until approved.

    `components` is a dict of any of basic / housing / transport / phone /
    other_allowance. Anything omitted is carried from the current structure, so
    a housing-only revision does not silently zero the basic.
    """
    from payroll.services import get_effective_salary_structure

    effective_from = _as_date(effective_from)
    unknown = set(components or {}) - set(SALARY_COMPONENTS)
    if unknown:
        raise ValidationError('Unknown salary component(s): %s' % ', '.join(sorted(unknown)))
    if not components:
        raise ValidationError('A salary revision has to change something.')

    current = get_effective_salary_structure(employee, effective_from)
    proposed, previous = {}, {}
    for field in SALARY_COMPONENTS:
        was = float(getattr(current, field, 0) or 0) if current else 0.0
        previous[field] = was
        proposed[field] = float(components.get(field, was) or 0)

    old_total = round(sum(previous.values()), 2)
    new_total = round(sum(proposed.values()), 2)
    if new_total == old_total and proposed == previous:
        raise ValidationError('The proposed structure is identical to the current one.')

    payload = {
        'components': proposed,
        'previous': previous,
        'previous_total': old_total,
        'proposed_total': new_total,
        'delta': round(new_total - old_total, 2),
        'delta_pct': round((new_total - old_total) / old_total * 100, 2) if old_total else None,
        'currency': getattr(employee, 'currency', 'AED'),
        'revision_type': revision_type or '',
    }
    return approvals.submit(
        REQUEST_SALARY_REVISION, employee, payload, actor=actor,
        summary=f'Salary {old_total:,.2f} → {new_total:,.2f} {payload["currency"]}',
        effective_date=effective_from, reason=reason)


@approvals.register_applier(REQUEST_SALARY_REVISION)
def _apply_salary_revision(request, actor=''):
    from attendance.models import SalaryStructure

    employee = request.employee
    if employee is None:
        raise ValidationError(
            'Salary structures exist for in-house employees only — RemoteEmployee '
            'has no salary_structures relation. This request cannot be applied.')

    payload = dict(request.payload or {})
    comps = payload.get('components') or {}
    last_approver = request.steps.filter(decision='approved').last()

    row = SalaryStructure.objects.create(
        employee=employee,
        effective_from=request.effective_date,
        currency=payload.get('currency') or getattr(employee, 'currency', 'AED'),
        status='approved',
        revision_reason=request.reason or '',
        revision_type=payload.get('revision_type') or '',
        approved_by=(last_approver.decided_by if last_approver else ''),
        approved_at=request.decided_at or timezone.now(),
        created_by=request.submitted_by or '',
        **{f: comps.get(f, 0) or 0 for f in SALARY_COMPONENTS})

    delta = payload.get('delta')
    timeline.record(
        employee, request.effective_date, 'salary', 'salary_revision',
        request.summary or 'Salary revised',
        detail=(f'{delta:+,.2f} ({payload.get("delta_pct")}%)' if delta is not None else ''),
        source_model='SalaryStructure', source_id=row.pk, actor=actor)
    return row


# ------------------------------------------------------------------- status

def change_status(employee, effective_from, new_status, reason='', actor=''):
    """Move somebody between employment states, with a record of why."""
    from attendance.models import Employee

    valid = dict(Employee.EMPLOYMENT_STATUS_CHOICES)
    if new_status not in valid:
        raise ValidationError('Unknown employment status "%s". Known: %s'
                              % (new_status, ', '.join(sorted(valid))))
    old = getattr(employee, 'employment_status', '') or ''
    if old == new_status:
        raise ValidationError('Already %s.' % valid[new_status])

    return approvals.submit(
        REQUEST_STATUS_CHANGE, employee,
        {'from': old, 'to': new_status,
         'from_label': valid.get(old, old), 'to_label': valid[new_status]},
        actor=actor,
        summary=f'Status {valid.get(old, old or "—")} → {valid[new_status]}',
        effective_date=_as_date(effective_from), reason=reason)


@approvals.register_applier(REQUEST_STATUS_CHANGE)
def _apply_status_change(request, actor=''):
    from attendance.models import EmploymentHistory

    employee = request.employee or request.remote_employee
    payload = dict(request.payload or {})
    new_status = payload.get('to')

    employee.employment_status = new_status
    employee.save(update_fields=['employment_status'])

    if request.employee is not None:
        # EmploymentHistory is in-house only, and pre-dates this engine. Written
        # to as well as the timeline so anything already reading it keeps working.
        EmploymentHistory.objects.create(
            employee=request.employee, change_type='status',
            effective_date=request.effective_date,
            previous_value=payload.get('from_label') or payload.get('from') or '',
            new_value=payload.get('to_label') or new_status,
            reason=request.reason or '', changed_by=actor or request.submitted_by or '')

    timeline.record(employee, request.effective_date, 'employment', 'status_change',
                    request.summary or 'Status changed', detail=request.reason or '',
                    source_model='Employee', source_id=employee.pk, actor=actor)
    return employee
