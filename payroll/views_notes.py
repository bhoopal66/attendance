"""
Notes & Timeline API — Phase C.

Two GET/POST JSON endpoints backing the per-employee "Notes & Timeline"
modal on the payroll dashboard (Per-Employee Final Salary table):

  GET  /payroll/api/notes/<emp_type>/<employee_id>/   -> get_employee_notes
  POST /payroll/api/notes/add/                        -> add_employee_note

The timeline merges four sources for the given employee, newest first:
  - Manual notes            (payroll.PayrollNote — new in this phase)
  - Deduction entry events   (attendance.AuditLog, model_name='deductionentry',
                               matched to this employee via the audit row's
                               object_repr, which is a snapshot string of
                               "<employee name> — <category> ...")
  - Marked-as-Paid events    (payroll.PaidSalaryRecord.paid_at / paid_by)
  - Carryover events         (payroll.DeductionCarryover.created_at, and
                               .skipped_at when is_skipped)

Access: same 'payroll' section grant as the rest of the payroll dashboard.

Kept in a separate file so the payroll/views.py monolith is not touched.
Deploy as: payroll/views_notes.py
"""

import json
import logging

from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods

from attendance.models import Employee, RemoteEmployee
from attendance.views.utils import section_required

from .models import DeductionCarryover, PaidSalaryRecord, PayrollNote

logger = logging.getLogger('attendance')

MONTH_NAMES = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
               'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']


def _resolve_employee(emp_type, employee_id):
    if emp_type == 'inhouse':
        return get_object_or_404(Employee, id=employee_id)
    elif emp_type == 'remote':
        return get_object_or_404(RemoteEmployee, id=employee_id)
    return None


def _build_timeline(emp_type, employee):
    entries = []

    # ---- Manual notes ----
    note_kwargs = {'employee': employee} if emp_type == 'inhouse' else {'remote_employee': employee}
    for n in PayrollNote.objects.filter(**note_kwargs):
        entries.append({
            'type': 'note',
            'timestamp': n.created_at.isoformat(),
            'title': 'Note',
            'detail': n.text,
            'actor': n.created_by or '—',
        })

    # ---- Deduction entry events (via audit log) ----
    try:
        from attendance.models import AuditLog
        prefix = f"{employee.name} — "
        ded_rows = AuditLog.objects.filter(model_name='deductionentry', object_repr__startswith=prefix)
        action_labels = {
            AuditLog.ACTION_CREATE: 'Deduction added',
            AuditLog.ACTION_UPDATE: 'Deduction updated',
            AuditLog.ACTION_DELETE: 'Deduction removed',
        }
        for a in ded_rows:
            detail = a.object_repr[len(prefix):]
            if a.action == AuditLog.ACTION_UPDATE and a.changes:
                changed_fields = ', '.join(a.changes.keys())
                detail = f"{detail} (changed: {changed_fields})"
            entries.append({
                'type': 'deduction',
                'timestamp': a.timestamp.isoformat(),
                'title': action_labels.get(a.action, a.get_action_display()),
                'detail': detail,
                'actor': a.actor or 'system',
            })
    except Exception:
        logger.exception('Failed to build deduction timeline events for %s #%s', emp_type, getattr(employee, 'id', '?'))

    # ---- Marked-as-Paid events ----
    paid_kwargs = {'employee': employee} if emp_type == 'inhouse' else {'remote_employee': employee}
    for p in PaidSalaryRecord.objects.filter(**paid_kwargs):
        month_label = f"{MONTH_NAMES[p.month]} {p.year}" if 1 <= p.month <= 12 else f"{p.month}/{p.year}"
        entries.append({
            'type': 'payment',
            'timestamp': p.paid_at.isoformat(),
            'title': f'Marked as Paid — {month_label}',
            'detail': f"{p.currency} {p.final_salary:,.2f}",
            'actor': p.paid_by or '—',
        })

    # ---- Carryover events ----
    co_kwargs = {'employee': employee} if emp_type == 'inhouse' else {'remote_employee': employee}
    for c in DeductionCarryover.objects.filter(**co_kwargs):
        entries.append({
            'type': 'carryover',
            'timestamp': c.created_at.isoformat(),
            'title': f'Carryover created — {c.from_month}/{c.from_year} → {c.to_month}/{c.to_year}',
            'detail': f"{c.currency} {c.overflow_amount:,.2f}",
            'actor': '—',
        })
        if c.is_skipped and c.skipped_at:
            entries.append({
                'type': 'carryover',
                'timestamp': c.skipped_at.isoformat(),
                'title': 'Carryover skipped',
                'detail': c.skip_reason or '—',
                'actor': c.skipped_by or '—',
            })

    entries.sort(key=lambda e: e['timestamp'], reverse=True)
    return entries


@login_required
@user_passes_test(section_required('payroll'), login_url='/report/')
@require_http_methods(['GET'])
def get_employee_notes(request, emp_type, employee_id):
    """Return the merged Notes & Timeline feed for one employee, newest first."""
    employee = _resolve_employee(emp_type, employee_id)
    if employee is None:
        return JsonResponse({'success': False, 'error': 'Invalid employee type'}, status=400)

    entries = _build_timeline(emp_type, employee)
    return JsonResponse({'success': True, 'employee_name': employee.name, 'entries': entries})


@login_required
@user_passes_test(section_required('payroll'), login_url='/report/')
@require_http_methods(['POST'])
def add_employee_note(request):
    """Create a manual PayrollNote for an employee."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    emp_type = data.get('emp_type')
    employee_id = data.get('employee_id')
    text = (data.get('text') or '').strip()

    if not all([emp_type, employee_id, text]):
        return JsonResponse({'success': False, 'error': 'Missing required fields'}, status=400)

    employee = _resolve_employee(emp_type, employee_id)
    if employee is None:
        return JsonResponse({'success': False, 'error': 'Invalid employee type'}, status=400)

    note_kwargs = {'employee_id': employee_id} if emp_type == 'inhouse' else {'remote_employee_id': employee_id}
    PayrollNote.objects.create(
        text=text,
        created_by=request.user.username,
        **note_kwargs,
    )
    logger.info("PayrollNote added: %s #%s by %s", emp_type, employee_id, request.user.username)
    return JsonResponse({'success': True})
