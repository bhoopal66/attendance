"""
Phase 3 - Loans & Salary Advances: the money.

Everything that decides what an employee actually repays lives here rather
than in a view, so it can be tested and so there is one implementation rather
than one per screen.

THE INTEGRATION, IN ONE PARAGRAPH
---------------------------------
Activating a loan writes one ordinary `DeductionEntry` per instalment
(`split_months=1`, category `loan_repayment`). Payroll then recovers it via the
code path it already uses for every other deduction. No payroll calculation
code is touched by this module. That is deliberate: the Phase 0 regression
baseline has not been captured yet, and a phase that rewrote the engine without
one would be indefensible. A loan changes what is deducted the same way adding
a deduction by hand does - by creating data.

Amounts are `Decimal` end to end. `payroll/views.py` works in floats and rounds
at the edges; a repayment schedule must not, because a schedule that is a fils
short leaves a loan that never reaches zero.
"""

import logging
from decimal import ROUND_DOWN, Decimal

from django.db import transaction
from django.utils import timezone

from attendance.audit import log_audit
from attendance.models import AuditLog, Recoverable

from .models import DeductionEntry, Loan, LoanInstallment

logger = logging.getLogger('payroll')

CENT = Decimal('0.01')
ZERO = Decimal('0.00')


# --------------------------------------------------------------- scheduling

def split_principal(principal, count):
    """Divide `principal` into `count` amounts that sum to it EXACTLY.

    The naive version - round(principal / count, 2) repeated `count` times - is
    wrong, and wrong in a way that only shows up later: 1000.00 over 3 months
    gives 333.33 three times, which is 999.99. The last fils is never recovered,
    the loan never closes, and someone eventually writes it off by hand.

    Here each instalment is the principal rounded DOWN to the fils, and the
    accumulated remainder is added to the FINAL instalment. Rounding down first
    guarantees the remainder is non-negative and smaller than `count` fils, so
    the adjustment is always a rounding correction and never a real change in
    what is owed.

    Returns a list of Decimals, length `count`, summing to `principal`.
    """
    principal = Decimal(principal).quantize(CENT)
    if count < 1:
        raise ValueError('count must be at least 1')
    if principal <= ZERO:
        raise ValueError('principal must be greater than zero')

    base = (principal / count).quantize(CENT, rounding=ROUND_DOWN)
    amounts = [base] * count
    remainder = principal - (base * count)
    amounts[-1] = amounts[-1] + remainder

    assert sum(amounts) == principal, (
        f'schedule {sum(amounts)} != principal {principal}')
    return amounts


def shift_month(year, month, offset):
    """(year, month) moved `offset` months. Month is 1-12."""
    idx = year * 12 + (month - 1) + offset
    y, m = divmod(idx, 12)
    return y, m + 1


def build_schedule(principal, count, start_year, start_month):
    """[(sequence, year, month, amount), ...] - consecutive months from the start."""
    amounts = split_principal(principal, count)
    out = []
    for i, amount in enumerate(amounts):
        y, m = shift_month(start_year, start_month, i)
        out.append((i + 1, y, m, amount))
    return out


@transaction.atomic
def generate_installments(loan, replace=False):
    """Create the loan's instalment rows.

    Refuses to touch a schedule that has already recovered or waived money -
    regenerating it would rewrite history that payroll has already acted on.
    """
    existing = list(loan.installments.all())
    if existing and not replace:
        return existing
    if existing:
        locked = [i for i in existing
                  if i.amount_recovered > ZERO
                  or i.status in (LoanInstallment.STATUS_RECOVERED,
                                  LoanInstallment.STATUS_WAIVED)]
        if locked:
            raise ValueError(
                f'{loan.reference} already has {len(locked)} instalment(s) that '
                'have been recovered or waived. The schedule cannot be rebuilt '
                'without rewriting what payroll has already done.')
        _unpost(existing)
        loan.installments.all().delete()

    rows = [
        LoanInstallment(loan=loan, sequence=seq, year=y, month=m, due_amount=amt)
        for seq, y, m, amt in build_schedule(
            loan.principal, loan.installment_count,
            loan.first_deduction_year, loan.first_deduction_month)
    ]
    LoanInstallment.objects.bulk_create(rows)
    return list(loan.installments.all())


# ------------------------------------------------------------- payroll link

def _employee_kwargs(loan):
    return ({'employee': loan.employee, 'remote_employee': None}
            if loan.employee_id else
            {'remote_employee': loan.remote_employee, 'employee': None})


def _unpost(installments):
    """Delete the payroll deductions behind these instalments.

    Only ever removes entries this module created (it goes through the
    instalment's own FK, never a category-wide query), and never removes one
    whose month has already been paid - that money has left the building.
    """
    removed = 0
    for inst in installments:
        entry = inst.deduction_entry
        if entry is None:
            continue
        if inst.amount_recovered > ZERO or inst.status == LoanInstallment.STATUS_RECOVERED:
            continue
        inst.deduction_entry = None
        inst.status = LoanInstallment.STATUS_SCHEDULED
        inst.save(update_fields=['deduction_entry', 'status', 'updated_at'])
        entry.delete()
        removed += 1
    return removed


@transaction.atomic
def post_installments(loan, actor=''):
    """Write one DeductionEntry per unposted instalment.

    One entry per month with `split_months=1`, rather than a single entry split
    over N months, because instalments are individually waivable and skippable.
    A single split entry would make "waive month 3" impossible to express.
    """
    posted = 0
    for inst in loan.installments.all():
        if inst.deduction_entry_id or inst.status in (
                LoanInstallment.STATUS_WAIVED, LoanInstallment.STATUS_SKIPPED):
            continue
        entry = DeductionEntry.objects.create(
            category=Loan.DEDUCTION_CODE,
            total_amount=inst.due_amount,
            currency=loan.currency,
            split_months=1,
            start_year=inst.year,
            start_month=inst.month,
            note=f'{loan.reference} instalment {inst.sequence}/{loan.installment_count}'
                 f' - {loan.description}'[:500],
            **_employee_kwargs(loan),
        )
        inst.deduction_entry = entry
        inst.status = LoanInstallment.STATUS_POSTED
        inst.save(update_fields=['deduction_entry', 'status', 'updated_at'])
        posted += 1
    return posted


@transaction.atomic
def activate(loan, actor=''):
    """Draft -> Active: build the schedule, post it, open the ledger row."""
    if loan.status == Loan.STATUS_ACTIVE:
        return loan
    if loan.is_closed:
        raise ValueError(f'{loan.reference} is {loan.get_status_display().lower()} '
                         'and cannot be reactivated.')
    generate_installments(loan)
    post_installments(loan, actor=actor)
    loan.status = Loan.STATUS_ACTIVE
    loan.activated_by = actor
    loan.activated_at = timezone.now()
    loan.save(update_fields=['status', 'activated_by', 'activated_at'])
    sync_recoverable(loan, actor=actor)
    log_audit(actor, AuditLog.ACTION_UPDATE, loan,
              changes={'status': ['draft', 'active']},
              note=f'Loan activated: {loan.installment_count} instalment(s), '
                   f'{loan.currency} {loan.principal}')
    logger.info('Loan %s activated by %s', loan.reference, actor)
    return loan


@transaction.atomic
def cancel(loan, actor='', reason=''):
    """Stop future recovery. Everything already recovered stays recovered."""
    pending = [i for i in loan.installments.all()
               if i.amount_recovered <= ZERO
               and i.status != LoanInstallment.STATUS_RECOVERED]
    removed = _unpost(pending)
    for inst in pending:
        inst.status = LoanInstallment.STATUS_SKIPPED
        inst.note = (inst.note or '') or 'Loan cancelled'
        inst.save(update_fields=['status', 'note', 'updated_at'])
    loan.status = Loan.STATUS_CANCELLED
    loan.closed_by = actor
    loan.closed_at = timezone.now()
    loan.closed_reason = (reason or '')[:255]
    loan.save(update_fields=['status', 'closed_by', 'closed_at', 'closed_reason'])
    sync_recoverable(loan, actor=actor)
    log_audit(actor, AuditLog.ACTION_UPDATE, loan,
              note=f'Loan cancelled ({removed} future deduction(s) withdrawn): {reason}'[:255])
    logger.info('Loan %s cancelled by %s, %s deductions withdrawn', loan.reference, actor, removed)
    return removed


@transaction.atomic
def waive_installment(loan, installment, actor='', reason=''):
    """Forgive one instalment. Reduces what is owed; does not recover it."""
    if installment.amount_recovered > ZERO or installment.status == LoanInstallment.STATUS_RECOVERED:
        raise ValueError('That instalment has already been recovered and cannot be waived.')
    _unpost([installment])
    installment.status = LoanInstallment.STATUS_WAIVED
    installment.note = (reason or 'Waived')[:255]
    installment.save(update_fields=['status', 'note', 'updated_at'])
    sync_recoverable(loan, actor=actor)
    log_audit(actor, AuditLog.ACTION_UPDATE, loan,
              note=f'Instalment {installment.sequence} waived: {reason}'[:255])
    return installment


# ----------------------------------------------------------------- recovery

def _paid_periods(loan):
    """{(year, month)} where this employee's payroll is locked AND paid in full.

    A partially-paid month is deliberately NOT counted. The deduction was
    applied to the calculated salary, but only part of that salary was actually
    disbursed, so treating the instalment as recovered would overstate what the
    employee has repaid.
    """
    from .models import PaidSalaryRecord
    qs = PaidSalaryRecord.objects.filter(**_employee_kwargs(loan))
    return {(r.year, r.month) for r in qs if not r.is_partial}


@transaction.atomic
def refresh_recovery(loan, actor=''):
    """Mark instalments recovered once their month has been paid in full.

    Read-mostly and idempotent: safe to call on every page load.
    """
    paid = _paid_periods(loan)
    changed = 0
    for inst in loan.installments.all():
        if inst.status in (LoanInstallment.STATUS_WAIVED, LoanInstallment.STATUS_SKIPPED):
            continue
        # An instalment whose deduction entry was deleted from the deductions
        # screen is NOT recovered, and is no longer posted either.
        #
        # NB: the FK is on_delete=SET_NULL, so deleting the entry clears
        # `deduction_entry_id` itself - testing `id and obj is None` here would
        # be dead code, and the instalment would sit at "Posted to payroll"
        # forever with nothing behind it, never recovering and never explaining
        # why. Test the id being gone while the status still claims otherwise.
        if inst.deduction_entry_id is None and inst.status == LoanInstallment.STATUS_POSTED:
            inst.status = LoanInstallment.STATUS_SCHEDULED
            inst.amount_recovered = ZERO
            inst.note = (inst.note or 'Payroll deduction was removed - not posted')[:255]
            inst.save(update_fields=['status', 'amount_recovered', 'note', 'updated_at'])
            changed += 1
            continue
        should = inst.deduction_entry_id is not None and (inst.year, inst.month) in paid
        if should and inst.status != LoanInstallment.STATUS_RECOVERED:
            inst.amount_recovered = inst.due_amount
            inst.status = LoanInstallment.STATUS_RECOVERED
            inst.save(update_fields=['amount_recovered', 'status', 'updated_at'])
            changed += 1
        elif not should and inst.status == LoanInstallment.STATUS_RECOVERED:
            # The month was unmarked as paid. Reverse the credit rather than
            # leaving the loan showing a repayment that was undone.
            inst.amount_recovered = ZERO
            inst.status = (LoanInstallment.STATUS_POSTED
                           if inst.deduction_entry_id else LoanInstallment.STATUS_SCHEDULED)
            inst.save(update_fields=['amount_recovered', 'status', 'updated_at'])
            changed += 1

    if changed:
        # Settle FIRST, then sync. sync_recoverable maps the loan's status onto
        # the ledger row, so syncing before settling leaves a settled loan with
        # an 'active' Recoverable - the ledger would still show a debt.
        if loan.status == Loan.STATUS_ACTIVE and loan.outstanding <= ZERO:
            loan.status = Loan.STATUS_SETTLED
            loan.closed_at = timezone.now()
            loan.closed_by = actor or 'system'
            loan.closed_reason = 'Fully recovered'
            loan.save(update_fields=['status', 'closed_at', 'closed_by', 'closed_reason'])
            log_audit(actor, AuditLog.ACTION_UPDATE, loan, note='Loan fully recovered - settled')
        sync_recoverable(loan, actor=actor)
    return changed


# ------------------------------------------------------------- ledger sync

_STATUS_TO_RECOVERABLE = {
    Loan.STATUS_DRAFT: 'on_hold',
    Loan.STATUS_ACTIVE: 'active',
    Loan.STATUS_ON_HOLD: 'on_hold',
    Loan.STATUS_SETTLED: 'settled',
    Loan.STATUS_CANCELLED: 'waived',
}


def sync_recoverable(loan, actor=''):
    """Create or update the `Recoverable` this loan wraps.

    `Recoverable` remains the single answer to "what does this person owe us" -
    the employee profile page reads it, and Phase 3 does not want a second,
    competing answer. The loan owns the schedule; this copies the resulting
    balance onto the ledger row.
    """
    rec = loan.recoverable
    monthly = (loan.principal / loan.installment_count).quantize(CENT) \
        if loan.installment_count else loan.principal
    fields = {
        'recoverable_type': loan.purpose,
        'description': f'{loan.reference}: {loan.description}'[:255],
        'total_amount': loan.principal,
        'currency': loan.currency,
        'monthly_recovery': monthly,
        'recovery_start_year': loan.first_deduction_year,
        'recovery_start_month': loan.first_deduction_month,
        'amount_recovered': loan.total_recovered,
        'status': _STATUS_TO_RECOVERABLE.get(loan.status, 'active'),
        'notes': loan.note or '',
    }
    if rec is None:
        rec = Recoverable.objects.create(
            created_by=actor or loan.created_by or 'system',
            **_employee_kwargs(loan), **fields)
        Loan.objects.filter(pk=loan.pk).update(recoverable=rec)
        loan.recoverable = rec
    else:
        for k, v in fields.items():
            setattr(rec, k, v)
        rec.save(update_fields=list(fields.keys()))
    return rec


# ------------------------------------------------------------------ queries

def active_loans_for(employee, employee_type):
    kw = ({'employee': employee} if employee_type == 'inhouse'
          else {'remote_employee': employee})
    return Loan.objects.filter(status=Loan.STATUS_ACTIVE, **kw)


def installments_due(year, month):
    """Every instalment scheduled for a given month, across all loans."""
    return (LoanInstallment.objects
            .select_related('loan', 'loan__employee', 'loan__remote_employee')
            .filter(year=year, month=month)
            .exclude(status__in=[LoanInstallment.STATUS_WAIVED,
                                 LoanInstallment.STATUS_SKIPPED]))
