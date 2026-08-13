"""
Payroll Run lifecycle view — Phase 9.

Handles GET (status page + exception centre) and POST (status transitions)
for a single payroll month.  Kept in a separate file so the 238KB
payroll/views.py monolith is not touched.

URL: /payroll/run/<year>/<month>/   (name='payroll_run_detail')
"""

import logging
from datetime import date

from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_http_methods

from attendance.views.utils import section_required

logger = logging.getLogger('attendance')

MONTH_NAMES = [
    '', 'January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December',
]


# ── Exception centre ──────────────────────────────────────────────────────────

def _build_exception_report(year, month):
    """
    Returns a dict with two lists:
        blockers  — issues that should be resolved before locking
        warnings  — informational flags that don't block progression

    Each entry is a dict:
        { 'check': str, 'severity': 'blocker'|'warning', 'items': [str, ...] }
    """
    from attendance.models import Employee, Recoverable
    from payroll.models import ExchangeRate, DeductionEntry

    blockers = []
    warnings = []

    active_employees = Employee.objects.filter(is_active=True).select_related()

    # ── CHECK 1: Missing salary structure ────────────────────────────────────
    # An employee with no approved SalaryStructure AND Employee.salary null/0
    # is a blocker — payroll cannot be calculated.
    no_salary = []
    for emp in active_employees:
        has_structure = emp.salary_structures.filter(status='approved').exists()
        has_flat_salary = emp.salary and emp.salary > 0
        if not has_structure and not has_flat_salary:
            no_salary.append(f'{emp.name} ({emp.person_id})')
    if no_salary:
        blockers.append({
            'check':    'Missing salary structure',
            'severity': 'blocker',
            'detail':   'These employees have no approved salary structure and no flat salary set.',
            'items':    no_salary,
        })

    # ── CHECK 2: Missing bank details ─────────────────────────────────────────
    # AED employees without bank_name or bank_account_number cannot be paid.
    no_bank = []
    for emp in active_employees.filter(currency='AED'):
        if not emp.bank_name or not emp.bank_account_number:
            no_bank.append(f'{emp.name} ({emp.person_id})')
    if no_bank:
        blockers.append({
            'check':    'Missing bank details',
            'severity': 'blocker',
            'detail':   'These AED employees have incomplete bank information.',
            'items':    no_bank,
        })

    # ── CHECK 3: Missing exchange rate ────────────────────────────────────────
    # Foreign-currency employees (INR, NPR, etc.) need an ExchangeRate row for
    # the current month so the payroll calculation can convert to AED.
    foreign_currencies = (
        active_employees
        .exclude(currency='AED')
        .values_list('currency', flat=True)
        .distinct()
    )
    missing_rates = []
    for ccy in foreign_currencies:
        exists = ExchangeRate.objects.filter(currency=ccy, year=year, month=month).exists()
        if not exists:
            emp_names = [
                f'{e.name} ({e.person_id})'
                for e in active_employees.filter(currency=ccy)
            ]
            missing_rates.append(f'{ccy} (affects: {", ".join(emp_names)})')
    if missing_rates:
        blockers.append({
            'check':    'Missing exchange rate',
            'severity': 'blocker',
            'detail':   f'No exchange rate entered for {MONTH_NAMES[month]} {year}.',
            'items':    missing_rates,
        })

    # ── CHECK 4: Status anomalies ─────────────────────────────────────────────
    # Employees who are exited/inactive but have active deduction entries this month.
    inactive_with_deductions = []
    inactive_emps = Employee.objects.filter(is_active=False)
    for emp in inactive_emps:
        for de in DeductionEntry.objects.filter(employee=emp):
            if de.is_active_in(year, month):
                inactive_with_deductions.append(f'{emp.name} ({emp.person_id})')
                break
    if inactive_with_deductions:
        warnings.append({
            'check':    'Inactive employees with active deductions',
            'severity': 'warning',
            'detail':   'These employees are marked inactive but still have deduction entries active this month.',
            'items':    list(set(inactive_with_deductions)),
        })

    # ── CHECK 5: Open recoverables with no matching deduction this month ──────
    open_recoverables_no_deduction = []
    open_recs = Recoverable.objects.filter(
        status='active',
        employee__isnull=False,
        recovery_start_year__lte=year,
    ).select_related('employee')
    for rec in open_recs:
        # Check start date: only flag if recovery should have started
        start_idx  = rec.recovery_start_year * 12 + (rec.recovery_start_month - 1)
        target_idx = year * 12 + (month - 1)
        if start_idx > target_idx:
            continue  # recovery hasn't started yet
        # Check if there's a linked DeductionEntry active this month
        linked = rec.deduction_entries.filter(
            start_year__lte=year,
        ).exists()
        if not linked and rec.monthly_recovery > 0:
            emp_name = rec.employee.name if rec.employee else '?'
            open_recoverables_no_deduction.append(
                f'{emp_name} — {rec.get_recoverable_type_display()} '
                f'({rec.currency} {rec.outstanding_balance:,.2f} outstanding)'
            )
    if open_recoverables_no_deduction:
        warnings.append({
            'check':    'Open recoverables without linked deductions',
            'severity': 'warning',
            'detail':   'These recoverables are active but have no DeductionEntry linked. Recovery may not be processing.',
            'items':    open_recoverables_no_deduction,
        })

    return {
        'blockers': blockers,
        'warnings': warnings,
        'total_blockers': len(blockers),
        'total_warnings': len(warnings),
        'is_clear': len(blockers) == 0,
    }


# ── View ──────────────────────────────────────────────────────────────────────

@login_required
@user_passes_test(section_required('payroll'), login_url='/report/')
@require_http_methods(['GET', 'POST'])
def payroll_run_detail(request, year, month):
    """
    GET  — Render the PayrollRun status page with exception centre.
    POST — Advance the run one stage (JSON response).
    """
    from payroll.models import PayrollRun

    if month < 1 or month > 12:
        from django.http import Http404
        raise Http404('Invalid month')

    run = PayrollRun.get_or_create_for_month(year, month)

    if request.method == 'POST':
        return _handle_transition(request, run)

    exception_report = _build_exception_report(year, month)

    # Build ordered stage list for the stepper
    stages = [
        {
            'key':   status,
            'label': label,
            'order': idx,
            'is_current': status == run.status,
            'is_done':    idx < run.status_order,
            'is_future':  idx > run.status_order,
        }
        for idx, (status, label) in enumerate(PayrollRun.STATUS_CHOICES)
    ]

    context = {
        'run':              run,
        'year':             year,
        'month':            month,
        'month_name':       MONTH_NAMES[month],
        'stages':           stages,
        'exception_report': exception_report,
        'next_action':      run.next_action_label,
        'next_status':      run.next_status,
        'today':            date.today(),
    }
    return render(request, 'payroll/payroll_run.html', context)


def _handle_transition(request, run):
    """
    POST handler — dispatches on 'action' query param.

    Supported actions:
        advance     — advance the run one lifecycle stage (JSON response)
        save_notes  — persist free-text notes on the run (JSON response)

    Returns JSON { ok, ... } or { ok: false, error }.
    """
    action = request.POST.get('action', '').strip()

    # ── save_notes ────────────────────────────────────────────────────────────
    if action == 'save_notes':
        notes = request.POST.get('notes', '')
        run.notes = notes
        run.save(update_fields=['notes', 'updated_at'])
        logger.info(
            'PayrollRun %s/%s notes updated by %s',
            run.year, run.month,
            request.user.username if request.user.is_authenticated else 'system',
        )
        return JsonResponse({'ok': True})

    # ── advance ───────────────────────────────────────────────────────────────
    if action != 'advance':
        return JsonResponse({'ok': False, 'error': 'Unknown action.'}, status=400)

    username = request.user.username if request.user.is_authenticated else 'system'
    ok, result = run.advance(username)

    if ok:
        logger.info(
            'PayrollRun %s/%s advanced to %s by %s',
            run.year, run.month, result, username,
        )
        return JsonResponse({
            'ok':             True,
            'status':         run.status,
            'status_display': run.get_status_display(),
            'next_action':    run.next_action_label,
        })
    else:
        return JsonResponse({'ok': False, 'error': result}, status=400)
