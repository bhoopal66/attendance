"""
Phase 3 - Loans & Salary Advances: the screens.

All money decisions live in `payroll/services_loans.py`; this module validates
input, calls the service, and renders. Kept out of `payroll/views.py`, which
Phase 1 exists to stop growing.

Access: the 'payroll' section grant, same gate as the payroll dashboard.
Every state change is written to AuditLog by the service layer.
"""

import json
import logging
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from attendance.models import AuditLog, Employee, RemoteEmployee
from attendance.audit import log_audit
from attendance.views.utils import MONTH_NAMES, section_required

from . import services_loans as svc
from .models import Loan, LoanInstallment

logger = logging.getLogger('payroll')

# attendance.views.utils.MONTH_NAMES is 1-indexed - it carries a leading ''
# so MONTH_NAMES[1] is 'January'. Indexing it [month - 1] silently yields the
# previous month, and December would fall off the end.
_MONTH_LABEL = {i: (MONTH_NAMES[i] if i < len(MONTH_NAMES) else str(i))
                for i in range(1, 13)}


def _period_label(year, month):
    name = _MONTH_LABEL.get(month) or str(month)
    return f'{name[:3]} {year}'


def _selectable_employees():
    """Everyone who can hold a loan.

    Applies the same `tcr_id` exclusion the payroll dashboard uses: a person
    who exists as both an active in-house Employee and a RemoteEmployee is one
    person, paid through the in-house record, and must not appear twice.
    """
    inhouse = list(Employee.objects.filter(is_active=True).order_by('name'))
    tcr = {(e.tcr_id or '').strip() for e in inhouse if (e.tcr_id or '').strip()}
    remote_qs = RemoteEmployee.objects.filter(is_active=True)
    if tcr:
        remote_qs = remote_qs.exclude(tcr_id__in=tcr)
    out = [{'key': f'inhouse_{e.id}', 'id': e.id, 'type': 'inhouse',
            'name': e.name, 'tcr': e.tcr_id or '', 'currency': e.currency}
           for e in inhouse]
    out += [{'key': f'remote_{e.id}', 'id': e.id, 'type': 'remote',
             'name': e.name, 'tcr': e.tcr_id or '', 'currency': e.currency}
            for e in remote_qs.order_by('name')]
    out.sort(key=lambda r: r['name'].lower())
    return out


def _serialise_installment(i):
    return {
        'id': i.id,
        'sequence': i.sequence,
        'year': i.year,
        'month': i.month,
        'period': _period_label(i.year, i.month),
        'due_amount': float(i.due_amount),
        'amount_recovered': float(i.amount_recovered),
        'outstanding': float(i.outstanding),
        'status': i.status,
        'status_label': i.get_status_display(),
        'posted': i.deduction_entry_id is not None,
        'note': i.note,
    }


def _serialise_loan(loan, installments=None):
    person = loan.person
    insts = installments if installments is not None else list(loan.installments.all())
    return {
        'id': loan.id,
        'reference': loan.reference,
        'employee_name': person.name if person else '—',
        'employee_type': loan.employee_type,
        'employee_id': person.id if person else None,
        'purpose': loan.purpose,
        'purpose_label': loan.get_purpose_display(),
        'description': loan.description,
        'principal': float(loan.principal),
        'currency': loan.currency,
        'installment_count': loan.installment_count,
        'first_period': _period_label(loan.first_deduction_year, loan.first_deduction_month),
        'first_year': loan.first_deduction_year,
        'first_month': loan.first_deduction_month,
        'status': loan.status,
        'status_label': loan.get_status_display(),
        'note': loan.note,
        'recovered': float(sum((i.amount_recovered for i in insts), Decimal('0.00'))),
        'waived': float(sum((i.due_amount for i in insts
                             if i.status == LoanInstallment.STATUS_WAIVED), Decimal('0.00'))),
        'outstanding': float(loan.outstanding),
        'progress_pct': (round(float(sum((i.amount_recovered for i in insts), Decimal('0.00')))
                               / float(loan.principal) * 100, 1)
                         if loan.principal else 0.0),
        'installments': [_serialise_installment(i) for i in insts],
        'created_by': loan.created_by,
        'created_at': loan.created_at.strftime('%d %b %Y') if loan.created_at else '',
        'closed_reason': loan.closed_reason,
    }


def _load(request, refresh=False):
    """Resolve the loan named by `id` in the JSON body."""
    try:
        data = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return None, {}
    try:
        loan = Loan.objects.select_related(
            'employee', 'remote_employee', 'recoverable').get(id=int(data.get('id')))
    except (Loan.DoesNotExist, TypeError, ValueError):
        return None, data
    if refresh:
        svc.refresh_recovery(loan, actor=request.user.username)
        loan.refresh_from_db()
    return loan, data


# ------------------------------------------------------------------- pages

@login_required
@user_passes_test(section_required('payroll'), login_url='/report/')
def loans(request):
    """Loans & Salary Advances."""
    qs = (Loan.objects
          .select_related('employee', 'remote_employee')
          .prefetch_related('installments')
          .order_by('-created_at'))

    rows = []
    for loan in qs:
        # Idempotent and read-mostly: brings each loan's recovery state up to
        # date with whichever months have since been paid, so the page never
        # shows a stale balance.
        if loan.status == Loan.STATUS_ACTIVE:
            svc.refresh_recovery(loan, actor=request.user.username)
            loan.refresh_from_db()
        rows.append(_serialise_loan(loan))

    active = [r for r in rows if r['status'] == Loan.STATUS_ACTIVE]
    return render(request, 'payroll/loans.html', {
        'loans': rows,
        'employees': _selectable_employees(),
        'purpose_choices': Loan.PURPOSE_CHOICES,
        'stat_active': len(active),
        'stat_draft': sum(1 for r in rows if r['status'] == Loan.STATUS_DRAFT),
        'stat_settled': sum(1 for r in rows if r['status'] == Loan.STATUS_SETTLED),
        'stat_outstanding': round(sum(r['outstanding'] for r in active
                                      if r['currency'] == 'AED'), 2),
    })


# ---------------------------------------------------------------- endpoints

@login_required
@user_passes_test(section_required('payroll'), login_url='/report/')
@require_http_methods(["POST"])
def loan_preview(request):
    """Schedule preview for the form. Reads and writes nothing."""
    try:
        data = json.loads(request.body or '{}')
        principal = Decimal(str(data.get('principal')))
        count = int(data.get('installment_count') or 1)
        year = int(data.get('first_year'))
        month = int(data.get('first_month'))
    except (json.JSONDecodeError, InvalidOperation, TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Enter an amount, a number of months and a start month.'}, status=400)

    if principal <= 0:
        return JsonResponse({'success': False, 'error': 'Amount must be greater than zero.'}, status=400)
    if count < 1 or count > 120:
        return JsonResponse({'success': False, 'error': 'Instalments must be between 1 and 120.'}, status=400)
    if not 1 <= month <= 12:
        return JsonResponse({'success': False, 'error': 'Start month must be 1-12.'}, status=400)
    if principal / count < Decimal('0.01'):
        return JsonResponse({
            'success': False,
            'error': f'{count} instalments of {principal} would be under 0.01 each.'}, status=400)

    rows = [{'sequence': s, 'year': y, 'month': m,
             'period': _period_label(y, m), 'due_amount': float(a)}
            for s, y, m, a in svc.build_schedule(principal, count, year, month)]
    return JsonResponse({'success': True, 'schedule': rows,
                         'total': float(sum(Decimal(str(r['due_amount'])) for r in rows))})


@login_required
@user_passes_test(section_required('payroll'), login_url='/report/')
@require_http_methods(["POST"])
def loan_save(request):
    """Create a draft loan, or edit one that has not been activated."""
    try:
        data = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    loan_id = data.get('id')
    if loan_id:
        try:
            loan = Loan.objects.get(id=int(loan_id))
        except (Loan.DoesNotExist, TypeError, ValueError):
            return JsonResponse({'success': False, 'error': 'Loan not found'}, status=404)
        if loan.status != Loan.STATUS_DRAFT:
            return JsonResponse({
                'success': False,
                'error': f'{loan.reference} is {loan.get_status_display().lower()}. '
                         'Only a draft loan can be edited — cancel it and raise a new '
                         'one rather than changing terms money has already moved under.',
            }, status=400)
        person, emp_type = loan.person, loan.employee_type
    else:
        loan = Loan()
        emp_type = data.get('employee_type')
        try:
            if emp_type == 'inhouse':
                person = Employee.objects.get(id=int(data.get('employee_id')))
            elif emp_type == 'remote':
                person = RemoteEmployee.objects.get(id=int(data.get('employee_id')))
            else:
                return JsonResponse({'success': False, 'error': 'Choose an employee.'}, status=400)
        except (Employee.DoesNotExist, RemoteEmployee.DoesNotExist, TypeError, ValueError):
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        loan.employee = person if emp_type == 'inhouse' else None
        loan.remote_employee = person if emp_type == 'remote' else None
        loan.currency = person.currency
        loan.created_by = request.user.username

    try:
        loan.principal = Decimal(str(data.get('principal')))
        loan.installment_count = int(data.get('installment_count') or 1)
        loan.first_deduction_year = int(data.get('first_year'))
        loan.first_deduction_month = int(data.get('first_month'))
    except (InvalidOperation, TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Amount, instalments and start month must be numbers.'}, status=400)

    loan.purpose = data.get('purpose') or 'advance'
    loan.description = (data.get('description') or '').strip()[:255]
    loan.note = (data.get('note') or '').strip()
    if not loan.description:
        return JsonResponse({'success': False, 'error': 'Add a short description of what this loan is for.'}, status=400)

    try:
        loan.full_clean(exclude=['reference', 'created_at', 'recoverable'])
    except ValidationError as exc:
        msgs = exc.message_dict
        first = next(iter(msgs.values()))[0] if msgs else 'Invalid loan.'
        return JsonResponse({'success': False, 'error': first}, status=400)

    creating = loan.pk is None
    loan.save()
    # Rebuild the draft schedule so the saved terms and the schedule cannot
    # disagree. Safe: a draft has posted and recovered nothing.
    svc.generate_installments(loan, replace=True)

    log_audit(request.user.username,
              AuditLog.ACTION_CREATE if creating else AuditLog.ACTION_UPDATE, loan,
              note=('Loan drafted' if creating else 'Draft loan edited') +
                   f': {loan.currency} {loan.principal} over {loan.installment_count} month(s)')
    logger.info('Loan %s %s by %s', loan.reference,
                'created' if creating else 'updated', request.user.username)
    return JsonResponse({'success': True, 'loan': _serialise_loan(loan)})


@login_required
@user_passes_test(section_required('payroll'), login_url='/report/')
@require_http_methods(["POST"])
def loan_activate(request):
    """Draft -> Active. Writes the payroll deductions."""
    loan, _ = _load(request)
    if loan is None:
        return JsonResponse({'success': False, 'error': 'Loan not found'}, status=404)
    try:
        svc.activate(loan, actor=request.user.username)
    except (ValueError, ValidationError) as exc:
        return JsonResponse({'success': False, 'error': str(exc)}, status=400)
    loan.refresh_from_db()
    return JsonResponse({'success': True, 'loan': _serialise_loan(loan)})


@login_required
@user_passes_test(section_required('payroll'), login_url='/report/')
@require_http_methods(["POST"])
def loan_cancel(request):
    """Stop future recovery. Anything already recovered stays recovered."""
    loan, data = _load(request, refresh=True)
    if loan is None:
        return JsonResponse({'success': False, 'error': 'Loan not found'}, status=404)
    if loan.is_closed:
        return JsonResponse({'success': False,
                             'error': f'{loan.reference} is already '
                                      f'{loan.get_status_display().lower()}.'}, status=400)
    try:
        withdrawn = svc.cancel(loan, actor=request.user.username,
                               reason=(data.get('reason') or '').strip())
    except (ValueError, ValidationError) as exc:
        return JsonResponse({'success': False, 'error': str(exc)}, status=400)
    loan.refresh_from_db()
    return JsonResponse({'success': True, 'withdrawn': withdrawn,
                         'loan': _serialise_loan(loan)})


@login_required
@user_passes_test(section_required('payroll'), login_url='/report/')
@require_http_methods(["POST"])
def loan_waive(request):
    """Forgive one instalment."""
    loan, data = _load(request, refresh=True)
    if loan is None:
        return JsonResponse({'success': False, 'error': 'Loan not found'}, status=404)
    try:
        inst = loan.installments.get(id=int(data.get('installment_id')))
    except (LoanInstallment.DoesNotExist, TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Instalment not found'}, status=404)
    try:
        svc.waive_installment(loan, inst, actor=request.user.username,
                              reason=(data.get('reason') or '').strip())
    except (ValueError, ValidationError) as exc:
        return JsonResponse({'success': False, 'error': str(exc)}, status=400)
    loan.refresh_from_db()
    return JsonResponse({'success': True, 'loan': _serialise_loan(loan)})


@login_required
@user_passes_test(section_required('payroll'), login_url='/report/')
@require_http_methods(["POST"])
def loan_delete(request):
    """Delete a draft loan outright. Refused once it has been activated."""
    loan, _ = _load(request)
    if loan is None:
        return JsonResponse({'success': False, 'error': 'Loan not found'}, status=404)
    if loan.status != Loan.STATUS_DRAFT:
        return JsonResponse({
            'success': False,
            'error': f'{loan.reference} has been activated. Cancel it instead — '
                     'deleting would erase a repayment history payroll has acted on.',
        }, status=400)
    ref, pk = loan.reference, loan.id
    loan.delete()
    loan.pk = pk
    log_audit(request.user.username, AuditLog.ACTION_DELETE, loan,
              note=f'Draft loan deleted: {ref}')
    logger.info('Draft loan %s deleted by %s', ref, request.user.username)
    return JsonResponse({'success': True})
