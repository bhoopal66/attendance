"""
Payroll re-run — re-open a month and recalculate it from scratch.

WHAT THIS DELIBERATELY BREAKS
-----------------------------
The payroll lifecycle is forward-only by design, and `PaidSalaryRecord` /
`FrozenPayrollMonth` have been treated throughout this system as permanently
immutable: once a month is locked, its figures are what they are, and later
changes to attendance or salary must not move them.

Re-opening reverses that on purpose. It was chosen explicitly - delete and
start clean, any month, any stage - over versioning the snapshots. This module
is the single place that does it, so the blast radius is one function rather
than a capability sprinkled through the views.

WHAT IT DESTROYS
----------------
For the chosen month:
  * every `PaidSalaryRecord` - the locked per-employee figures AND the record
    of what was actually disbursed, including payment method and splits;
  * the `FrozenPayrollMonth` snapshot, if the month was frozen.

Both are gone permanently. An already-issued payslip becomes unreconcilable
against the system.

WHAT IT PRESERVES, AND WHY
--------------------------
A summary of the destroyed snapshot - employee count, totals per currency,
who locked it, who marked it paid, when - is written to `AuditLog` BEFORE
anything is deleted. That is not a hedge against the decision; the snapshot
still goes. It is the difference between "January was re-run on 15 Aug by
bhoopal, replacing figures totalling AED 118,400 across 44 employees" and no
answer at all.

Deduction entries, loans, carryovers and paid-holiday declarations are NOT
touched. They are inputs to the calculation, not results of it - deleting them
would change what the re-run computes rather than letting it recompute. Loan
instalments un-mark themselves as recovered automatically, because
`services_loans.refresh_recovery` reads whether the month is paid.
"""

import logging
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from attendance.audit import log_audit
from attendance.models import AuditLog

logger = logging.getLogger('payroll')

ZERO = Decimal('0.00')


def describe_month(year, month):
    """What a re-run of this month would destroy. Reads only.

    Shown to the operator before they confirm, and written to the audit log
    before anything is deleted.
    """
    from .models import FrozenPayrollMonth, PaidSalaryRecord, PayrollRun

    records = list(PaidSalaryRecord.objects
                   .select_related('employee', 'remote_employee')
                   .filter(year=year, month=month))
    frozen = FrozenPayrollMonth.objects.filter(year=year, month=month).first()
    run = PayrollRun.objects.filter(year=year, month=month).first()

    totals, paid_totals = {}, {}
    for r in records:
        emp = r.employee or r.remote_employee
        cur = getattr(emp, 'currency', 'AED') or 'AED'
        totals[cur] = totals.get(cur, ZERO) + (r.final_salary or ZERO)
        paid_totals[cur] = paid_totals.get(cur, ZERO) + (r.effective_amount_paid or ZERO)

    return {
        'year': year, 'month': month,
        'run_status': run.status if run else None,
        'run_status_label': run.get_status_display() if run else 'No run record',
        'reopened_count': run.reopened_count if run else 0,
        'paid_records': len(records),
        'partial_records': sum(1 for r in records if r.is_partial),
        'net_totals': {k: str(v) for k, v in totals.items()},
        'disbursed_totals': {k: str(v) for k, v in paid_totals.items()},
        'is_frozen': frozen is not None,
        'frozen_by': frozen.frozen_by if frozen else '',
        'frozen_at': frozen.frozen_at.isoformat() if frozen else None,
        'locked_by': run.locked_by if run else '',
        'locked_at': run.locked_at.isoformat() if run and run.locked_at else None,
        'paid_by': run.paid_by if run else '',
        'paid_at': run.paid_at.isoformat() if run and run.paid_at else None,
        'employees': sorted(
            ((r.employee or r.remote_employee).name if (r.employee or r.remote_employee) else '?')
            for r in records),
    }


@transaction.atomic
def reopen_run(year, month, actor, reason):
    """Re-open a month: destroy its locked figures and reset the run to Draft.

    Refuses without a reason. Everything else is permitted, including a month
    that has been paid or posted - that was the explicit choice.
    """
    from .models import FrozenPayrollMonth, PaidSalaryRecord, PayrollRun

    reason = (reason or '').strip()
    if not reason:
        raise ValueError(
            'A reason is required. Re-opening deletes the locked figures for this '
            'month, and that needs an explanation attached to it.')

    summary = describe_month(year, month)

    run, _ = PayrollRun.objects.get_or_create(
        year=year, month=month, defaults={'status': PayrollRun.STATUS_DRAFT})

    # Written BEFORE the delete. Once the snapshot is gone this line is the
    # only record of what the month originally paid.
    log_audit(actor, AuditLog.ACTION_DELETE, run,
              changes={'status': [summary['run_status'] or 'none', PayrollRun.STATUS_DRAFT]},
              note=(f'PAYROLL RE-RUN {month:02d}/{year}: destroyed '
                    f'{summary["paid_records"]} paid record(s), net '
                    f'{summary["net_totals"]}, disbursed {summary["disbursed_totals"]}'
                    f'{", frozen snapshot" if summary["is_frozen"] else ""}. '
                    f'Was {summary["run_status_label"]}, locked by '
                    f'{summary["locked_by"] or "-"}. Reason: {reason}')[:255])

    deleted_paid, _ = PaidSalaryRecord.objects.filter(year=year, month=month).delete()
    deleted_frozen, _ = FrozenPayrollMonth.objects.filter(year=year, month=month).delete()

    # Back to the start, with every stage stamp cleared. Leaving stale
    # approved_by / paid_at on a Draft run would show a month as approved by
    # someone who approved a different set of figures.
    run.status = PayrollRun.STATUS_DRAFT
    for field in ('prepared_by', 'reviewed_by', 'approved_by', 'locked_by',
                  'paid_by', 'posted_by'):
        setattr(run, field, '')
    for field in ('prepared_at', 'reviewed_at', 'approved_at', 'locked_at',
                  'paid_at', 'posted_at'):
        setattr(run, field, None)
    run.reopened_count = (run.reopened_count or 0) + 1
    run.reopened_by = actor
    run.reopened_at = timezone.now()
    run.reopen_reason = reason
    run.save()

    logger.warning(
        'PAYROLL RE-RUN %s/%s by %s — deleted %s paid record(s), %s frozen snapshot(s). '
        'Run reset to draft (re-open #%s). Reason: %s',
        month, year, actor, deleted_paid, deleted_frozen, run.reopened_count, reason)

    return {
        'run': run,
        'summary': summary,
        'deleted_paid_records': deleted_paid,
        'deleted_frozen': deleted_frozen,
        'reopened_count': run.reopened_count,
    }
