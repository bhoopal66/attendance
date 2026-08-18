"""The approval engine: submit, decide, and only then apply.

THE SHAPE
    submit()  freezes the values and builds the steps from the configured chain
    decide()  records one approver's answer and advances or rejects
    apply     runs ONLY after the last step approves, through a registered
              applier for that request type

WHY APPLY IS SEPARATE FROM APPROVE
----------------------------------
Approving is a person's decision; applying is the system's consequence. They
fail for different reasons — an approval cannot fail, an application can (an
overlapping assignment, a locked payroll month). Keeping `decided_at` and
`applied_at` apart means a request that was properly approved but could not take
effect is visible as exactly that, instead of silently looking approved while
nothing happened.

NO CHAIN CONFIGURED
-------------------
If no chain exists for a request type, submit() REFUSES. The tempting
alternative — auto-approve when nobody is configured — turns a missing
configuration into an invisible bypass of the entire approval system.
"""
import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger('attendance')

_APPLIERS = {}


def register_applier(request_type):
    """Bind a request type to the function that makes it real."""
    def deco(fn):
        _APPLIERS[request_type] = fn
        return fn
    return deco


def _person(employee):
    from attendance.models import Employee
    if isinstance(employee, Employee):
        return {'employee': employee, 'remote_employee': None}
    return {'employee': None, 'remote_employee': employee}


def chain_for(request_type, company=None):
    """The chain for this type and entity, falling back to the all-entity one."""
    from attendance.models import ApprovalChain
    qs = ApprovalChain.objects.filter(request_type=request_type, is_active=True)
    return (qs.filter(company=company).first() if company else None) or \
        qs.filter(company__isnull=True).first()


@transaction.atomic
def submit(request_type, employee, payload, actor='', summary='',
           effective_date=None, reason='', company=None):
    """Freeze the values and open the approval steps. Returns the request."""
    from attendance.models import ApprovalRequest, ApprovalStep

    company = company or getattr(employee, 'company', None)
    chain = chain_for(request_type, company)
    if chain is None:
        raise ValidationError(
            'No approval chain is configured for "%s". Configure one before '
            'submitting — an unconfigured type must not approve itself.' % request_type)

    steps = list(chain.steps.all().order_by('sequence'))
    if not steps:
        raise ValidationError(
            'The approval chain for "%s" has no steps. An empty chain would '
            'approve on submission.' % request_type)

    req = ApprovalRequest.objects.create(
        request_type=request_type, company=company,
        payload=dict(payload or {}), summary=summary or '',
        effective_date=effective_date, reason=reason or '',
        submitted_by=actor or '', **_person(employee))

    for s in steps:
        ApprovalStep.objects.create(
            request=req, sequence=s.sequence, role_required=s.role_required,
            label=s.label or '')
    return req


@transaction.atomic
def decide(request, approver, approved, comments='', role=''):
    """Record one decision. Advances, rejects, or applies if it was the last.

    Refuses out-of-order approval: the pending step is the only one that can be
    decided. Otherwise a director could approve before the manager has seen it,
    which makes the chain decorative.
    """
    from attendance.models import ApprovalRequest, ApprovalStep

    request.refresh_from_db()
    if request.status != ApprovalRequest.STATUS_PENDING:
        raise ValidationError('This request is already %s.' % request.status)

    step = request.pending_step
    if step is None:
        raise ValidationError('No step is waiting on a decision.')
    if role and step.role_required and role != step.role_required:
        raise ValidationError(
            'This step is waiting on %s, not %s.' % (step.role_required, role))

    step.decision = (ApprovalStep.DECISION_APPROVED if approved
                     else ApprovalStep.DECISION_REJECTED)
    step.decided_by = approver or ''
    step.decided_at = timezone.now()
    step.comments = comments or ''
    step.save(update_fields=['decision', 'decided_by', 'decided_at', 'comments'])

    if not approved:
        request.status = ApprovalRequest.STATUS_REJECTED
        request.decided_at = timezone.now()
        request.save(update_fields=['status', 'decided_at'])
        return request

    if request.pending_step is not None:
        return request                      # more approvers to go

    request.status = ApprovalRequest.STATUS_APPROVED
    request.decided_at = timezone.now()
    request.save(update_fields=['status', 'decided_at'])
    _apply(request, actor=approver)
    return request


def _apply(request, actor=''):
    applier = _APPLIERS.get(request.request_type)
    if applier is None:
        request.apply_error = (
            'Approved, but nothing is registered to apply "%s". The decision '
            'stands; the change has NOT taken effect.' % request.request_type)
        request.save(update_fields=['apply_error'])
        logger.error('no applier for approved request %s', request.pk)
        return
    try:
        applier(request, actor=actor)
        request.applied_at = timezone.now()
        request.apply_error = ''
        request.save(update_fields=['applied_at', 'apply_error'])
    except Exception as exc:                                    # noqa: BLE001
        # The approval is a fact and stays recorded. The failure is recorded
        # beside it rather than swallowed, so it shows as approved-not-applied.
        request.apply_error = '%s: %s' % (type(exc).__name__, exc)
        request.save(update_fields=['apply_error'])
        logger.exception('applying approved request %s failed', request.pk)


@transaction.atomic
def cancel(request, actor='', reason=''):
    from attendance.models import ApprovalRequest
    request.refresh_from_db()
    if request.status != ApprovalRequest.STATUS_PENDING:
        raise ValidationError('Only a pending request can be cancelled.')
    request.status = ApprovalRequest.STATUS_CANCELLED
    request.decided_at = timezone.now()
    request.reason = (request.reason + '\n' if request.reason else '') + \
        ('Cancelled by %s: %s' % (actor or 'unknown', reason or 'no reason given'))
    request.save(update_fields=['status', 'decided_at', 'reason'])
    return request


def pending_for_role(role, company=None):
    """Everything waiting on this role right now — the manager/HR inbox."""
    from attendance.models import ApprovalRequest
    qs = ApprovalRequest.objects.filter(
        status=ApprovalRequest.STATUS_PENDING,
        steps__decision='pending', steps__role_required=role).distinct()
    if company:
        qs = qs.filter(company=company)
    return [r for r in qs if r.pending_step and r.pending_step.role_required == role]


# --------------------------------------------------------------------------
# The one vertical slice: an assignment change, approved, applied, remembered.
# --------------------------------------------------------------------------
REQUEST_ASSIGNMENT_CHANGE = 'assignment_change'


@register_applier(REQUEST_ASSIGNMENT_CHANGE)
def _apply_assignment_change(request, actor=''):
    """Turn an approved request into real assignment history and a timeline line."""
    import datetime

    from attendance import services_assignments, services_timeline
    from attendance.models import Employee, EmployeeAssignment

    payload = dict(request.payload or {})
    change_type = payload.pop('change_type', EmployeeAssignment.CHANGE_OTHER)
    person = request.person

    # Managers arrive as ids in a frozen payload — resolve at apply time.
    for key in ('reporting_manager', 'functional_manager'):
        if key in payload:
            payload[key] = Employee.objects.filter(pk=payload[key]).first() if payload[key] else None

    eff = request.effective_date
    if isinstance(eff, str):
        eff = datetime.date.fromisoformat(eff)

    new = services_assignments.open_assignment(
        person, eff, change_type, payload,
        reason=request.reason, actor=actor or request.submitted_by,
        approved_by=(request.steps.filter(decision='approved').last().decided_by
                     if request.steps.filter(decision='approved').exists() else ''),
        approved_at=request.decided_at)

    services_timeline.record(
        person, eff,
        category='promotion' if change_type == EmployeeAssignment.CHANGE_PROMOTION else 'employment',
        event_type=change_type,
        title=request.summary or change_type.replace('_', ' ').title(),
        detail=request.reason or '',
        source_model='EmployeeAssignment', source_id=new.pk, actor=actor)
    return new
