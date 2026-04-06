"""
Payroll calculation views.
"""

import io
import json
import datetime
import calendar
import logging
from collections import defaultdict
from decimal import Decimal

from django.core.management import call_command
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required, user_passes_test

from attendance.models import (
    Employee, RemoteEmployee, Holiday,
    LeaveRequest, MonthlySummary,
)
from attendance.views.utils import (
    MONTH_CHOICES, MONTH_NAMES, YEAR_RANGE,
    get_selected_month_year, superuser_required,
)
from .models import PayrollAdjustment, Bank, BankSubmission

logger = logging.getLogger('payroll')


# ============================================
# Payroll Calculation Helpers
# ============================================

def _count_holidays(year, month, days_in_month):
    """Return total non-working days (Sundays + custom holidays) for a month.
    Uses a set to avoid double-counting Sundays that are also custom holidays.
    """
    non_working = set(
        day for day in range(1, days_in_month + 1)
        if datetime.date(year, month, day).weekday() == 6
    )
    for holiday in Holiday.objects.filter(date__year=year, date__month=month):
        non_working.add(holiday.date.day)
    return len(non_working)


def _leave_days_in_month(leave, month_start, month_end):
    """Return the effective paid leave days that fall within the given month.
    Pro-rates approved_days based on how much of the leave span falls in the month.
    """
    effective = leave.get_effective_days()
    if not effective:
        return 0
    # If entirely within the month, no pro-rating needed
    if leave.start_date >= month_start and leave.end_date <= month_end:
        return effective
    # Clip to month boundaries and pro-rate
    overlap_start = max(leave.start_date, month_start)
    overlap_end = min(leave.end_date, month_end)
    full_span = (leave.end_date - leave.start_date).days + 1
    overlap_span = (overlap_end - overlap_start).days + 1
    return round(effective * overlap_span / full_span)


def _get_commission(year, month, employee=None, remote_employee=None):
    """Calculate total commission from bank submissions for the period."""
    if employee:
        submissions = BankSubmission.objects.filter(
            employee=employee, year=year, month=month
        ).select_related('bank')
    else:
        submissions = BankSubmission.objects.filter(
            remote_employee=remote_employee, year=year, month=month
        ).select_related('bank')
    return float(sum(s.submission_count * s.bank.per_account_charge for s in submissions))


def _get_inhouse_payroll_row(emp, year, month, month_start, month_end, total_holidays):
    """Build a payroll data row for one in-house employee."""
    summary = MonthlySummary.objects.filter(
        employee=emp, year=year, month=month
    ).first()

    if summary:
        half_days = summary.half_days or 0
        full_days = summary.working_days - half_days
        effective_work_days = full_days + (half_days * 0.5)
        absent_days = summary.leave_days or 0
        late_days = summary.late_days or 0
    else:
        full_days = 0
        half_days = 0
        effective_work_days = 0.0
        absent_days = 0
        late_days = 0

    salary = float(emp.salary) if emp.salary else 0.0

    approved_leaves = LeaveRequest.objects.filter(
        employee=emp,
        status='approved',
        start_date__lte=month_end,
        end_date__gte=month_start,
    )
    paid_leave_days = sum(_leave_days_in_month(leave, month_start, month_end) for leave in approved_leaves)

    days_in_month = calendar.monthrange(year, month)[1]
    daily_rate = salary / days_in_month if salary > 0 else 0.0
    # Every 3 late days = 1 half-day deduction
    late_half_days = late_days // 3
    # Deduct absent days, half-day shortfalls, and late penalties from full salary
    total_deduction_days = absent_days + (half_days * 0.5) + (late_half_days * 0.5)
    deduction = daily_rate * total_deduction_days
    base_payroll = salary - deduction

    adjustments = PayrollAdjustment.objects.filter(employee=emp, year=year, month=month)
    incentives = float(
        adjustments.filter(adjustment_type='incentive').aggregate(total=Sum('amount'))['total'] or 0
    )
    reductions = float(
        adjustments.filter(adjustment_type='reduction').aggregate(total=Sum('amount'))['total'] or 0
    )
    commission = _get_commission(year, month, employee=emp)
    net_payroll = base_payroll + incentives + commission - reductions

    return {
        'employee': emp,
        'employee_type': 'inhouse',
        'salary': salary,
        'full_days': full_days,
        'half_days': half_days,
        'effective_work_days': round(effective_work_days, 1),
        'absent_days': absent_days,
        'late_days': late_days,
        'late_half_days': late_half_days,
        'paid_leave_days': paid_leave_days,
        'total_deduction_days': round(total_deduction_days, 1),
        'daily_rate': round(daily_rate, 2),
        'deduction': round(deduction, 2),
        'base_payroll': round(base_payroll, 2),
        'incentives': round(incentives, 2),
        'reductions': round(reductions, 2),
        'commission': round(commission, 2),
        'net_payroll': round(net_payroll, 2),
    }


def _get_sales_payroll_row(emp, year, month, emp_type, banks):
    """Build a payroll data row for a sales employee (commission-only, no attendance deductions)."""
    if emp_type == 'inhouse':
        submissions_qs = BankSubmission.objects.filter(employee=emp, year=year, month=month)
        adjustments = PayrollAdjustment.objects.filter(employee=emp, year=year, month=month)
    else:
        submissions_qs = BankSubmission.objects.filter(remote_employee=emp, year=year, month=month)
        adjustments = PayrollAdjustment.objects.filter(remote_employee=emp, year=year, month=month)

    bank_counts = {s.bank_id: s.submission_count for s in submissions_qs}
    bank_counts_list = [bank_counts.get(b.id, 0) for b in banks]
    commission = sum(bank_counts.get(b.id, 0) * float(b.per_account_charge) for b in banks)

    incentives = float(
        adjustments.filter(adjustment_type='incentive').aggregate(total=Sum('amount'))['total'] or 0
    )
    reductions = float(
        adjustments.filter(adjustment_type='reduction').aggregate(total=Sum('amount'))['total'] or 0
    )
    net_payroll = commission + incentives - reductions

    return {
        'employee': emp,
        'employee_type': emp_type,
        'bank_counts_list': bank_counts_list,
        'commission': round(commission, 2),
        'incentives': round(incentives, 2),
        'reductions': round(reductions, 2),
        'net_payroll': round(net_payroll, 2),
    }


def _build_section_totals(rows):
    """Sum net, incentives, reductions, commission across a list of payroll rows."""
    return (
        round(sum(r['net_payroll'] for r in rows), 2),
        round(sum(r['incentives'] for r in rows), 2),
        round(sum(r['reductions'] for r in rows), 2),
        round(sum(r.get('commission', 0) for r in rows), 2),
    )


# ============================================
# Main Dashboard
# ============================================

@login_required
@user_passes_test(superuser_required, login_url='/report/')
def payroll_dashboard(request):
    """Payroll dashboard showing Admin and Sales sections."""
    selected_month, selected_year = get_selected_month_year(request)
    _, days_in_month = calendar.monthrange(selected_year, selected_month)
    month_start = datetime.date(selected_year, selected_month, 1)
    month_end = datetime.date(selected_year, selected_month, days_in_month)

    total_holidays = _count_holidays(selected_year, selected_month, days_in_month)

    # Fetch active banks for sales spreadsheet
    banks = list(Bank.objects.filter(is_active=True).order_by('name'))
    banks_json = json.dumps([
        {'id': b.id, 'name': b.name, 'rate': float(b.per_account_charge)}
        for b in banks
    ])

    # --- Admin section (in-house Admin dept) ---
    admin_employees = Employee.objects.filter(
        department='Admin', is_active=True
    ).order_by('name')
    admin_data = [
        _get_inhouse_payroll_row(emp, selected_year, selected_month, month_start, month_end, total_holidays)
        for emp in admin_employees
    ]
    total_admin, admin_incentives_total, admin_reductions_total, _ = _build_section_totals(admin_data)

    # --- Sales section: in-house Sales employees (commission-only) ---
    sales_inhouse_employees = Employee.objects.filter(
        department='Sales', is_active=True
    ).order_by('name')
    sales_inhouse_data = [
        _get_sales_payroll_row(emp, selected_year, selected_month, 'inhouse', banks)
        for emp in sales_inhouse_employees
    ]
    total_sales_inhouse, _, _, _ = _build_section_totals(sales_inhouse_data)

    # --- Sales section: remote employees (commission-only) ---
    remote_employees = RemoteEmployee.objects.filter(is_active=True).order_by('name')
    remote_data = [
        _get_sales_payroll_row(emp, selected_year, selected_month, 'remote', banks)
        for emp in remote_employees
    ]
    total_remote, _, _, _ = _build_section_totals(remote_data)

    # Combined sales data for spreadsheet view
    all_sales_data = sales_inhouse_data + remote_data

    # Combined Sales totals
    all_sales_rows = sales_inhouse_data + remote_data
    total_sales, sales_incentives_total, sales_reductions_total, total_sales_commission = _build_section_totals(all_sales_rows)

    grand_total = round(total_admin + total_sales, 2)

    context = {
        'selected_month': selected_month,
        'selected_year': selected_year,
        'month_name': MONTH_NAMES[selected_month],
        'months': MONTH_CHOICES,
        'years': YEAR_RANGE,
        'total_holidays': total_holidays,
        # Admin
        'admin_data': admin_data,
        'total_admin': total_admin,
        'admin_incentives_total': admin_incentives_total,
        'admin_reductions_total': admin_reductions_total,
        # Sales (spreadsheet)
        'banks': banks,
        'banks_json': banks_json,
        'all_sales_data': all_sales_data,
        'total_sales_inhouse': total_sales_inhouse,
        'total_remote': total_remote,
        'total_sales': total_sales,
        'total_sales_commission': total_sales_commission,
        'sales_incentives_total': sales_incentives_total,
        'sales_reductions_total': sales_reductions_total,
        # Grand total
        'grand_total': grand_total,
    }

    return render(request, 'payroll/dashboard.html', context)


# ============================================
# Bank Management
# ============================================

@login_required
@user_passes_test(superuser_required, login_url='/report/')
def manage_banks(request):
    """Bank management page — list/add/edit/deactivate banks."""
    banks = Bank.objects.all().order_by('name')
    return render(request, 'payroll/banks.html', {'banks': banks})


@login_required
@user_passes_test(superuser_required, login_url='/report/')
@require_http_methods(["GET", "POST"])
def banks_api(request):
    """GET: list active banks. POST: add a new bank."""
    if request.method == 'GET':
        banks = Bank.objects.filter(is_active=True).order_by('name')
        data = [{'id': b.id, 'name': b.name, 'per_account_charge': float(b.per_account_charge)} for b in banks]
        return JsonResponse({'success': True, 'banks': data})

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    name = data.get('name', '').strip()
    per_account_charge = data.get('per_account_charge')

    if not name or per_account_charge is None:
        return JsonResponse({'success': False, 'error': 'Name and per_account_charge are required'}, status=400)

    if Bank.objects.filter(name__iexact=name).exists():
        return JsonResponse({'success': False, 'error': 'A bank with this name already exists'}, status=400)

    try:
        bank = Bank.objects.create(name=name, per_account_charge=Decimal(str(per_account_charge)))
    except (ValueError, TypeError) as e:
        return JsonResponse({'success': False, 'error': f'Invalid data: {e}'}, status=400)

    logger.info("Bank added: %s (AED %s/account) by %s", bank.name, bank.per_account_charge, request.user.username)
    return JsonResponse({'success': True, 'bank': {
        'id': bank.id, 'name': bank.name,
        'per_account_charge': float(bank.per_account_charge),
        'is_active': bank.is_active,
    }})


@login_required
@user_passes_test(superuser_required, login_url='/report/')
@require_http_methods(["POST"])
def bank_detail_api(request, bank_id):
    """Update or toggle a bank."""
    try:
        bank = Bank.objects.get(id=bank_id)
    except Bank.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Bank not found'}, status=404)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    action = data.get('action')

    if action == 'update':
        name = data.get('name', '').strip()
        per_account_charge = data.get('per_account_charge')
        if not name or per_account_charge is None:
            return JsonResponse({'success': False, 'error': 'Name and charge are required'}, status=400)
        # Check uniqueness excluding self
        if Bank.objects.filter(name__iexact=name).exclude(id=bank_id).exists():
            return JsonResponse({'success': False, 'error': 'Another bank with this name already exists'}, status=400)
        try:
            bank.name = name
            bank.per_account_charge = Decimal(str(per_account_charge))
            bank.save()
        except (ValueError, TypeError) as e:
            return JsonResponse({'success': False, 'error': f'Invalid data: {e}'}, status=400)
        logger.info("Bank updated: %s by %s", bank.name, request.user.username)
        return JsonResponse({'success': True, 'bank': {
            'id': bank.id, 'name': bank.name,
            'per_account_charge': float(bank.per_account_charge),
            'is_active': bank.is_active,
        }})

    elif action == 'toggle':
        bank.is_active = not bank.is_active
        bank.save()
        logger.info("Bank %s: %s by %s", 'activated' if bank.is_active else 'deactivated', bank.name, request.user.username)
        return JsonResponse({'success': True, 'is_active': bank.is_active})

    return JsonResponse({'success': False, 'error': 'Unknown action'}, status=400)


# ============================================
# API: Bank Submissions
# ============================================

@login_required
@user_passes_test(superuser_required, login_url='/report/')
def get_submissions(request, emp_type, employee_id):
    """Get bank submissions for an employee for a specific month."""
    try:
        year = int(request.GET.get('year', datetime.datetime.now().year))
        month = int(request.GET.get('month', datetime.datetime.now().month))
    except (ValueError, TypeError):
        return JsonResponse({'success': False, 'error': 'Invalid year/month'})

    if emp_type == 'inhouse':
        try:
            employee = Employee.objects.get(id=employee_id)
        except Employee.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        submissions_qs = BankSubmission.objects.filter(
            employee=employee, year=year, month=month
        ).select_related('bank')
    else:
        try:
            employee = RemoteEmployee.objects.get(id=employee_id)
        except RemoteEmployee.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        submissions_qs = BankSubmission.objects.filter(
            remote_employee=employee, year=year, month=month
        ).select_related('bank')

    submission_map = {s.bank_id: s.submission_count for s in submissions_qs}
    banks = Bank.objects.filter(is_active=True).order_by('name')

    data = []
    total_commission = 0.0
    for bank in banks:
        count = submission_map.get(bank.id, 0)
        commission = round(count * float(bank.per_account_charge), 2)
        total_commission += commission
        data.append({
            'bank_id': bank.id,
            'bank_name': bank.name,
            'per_account_charge': float(bank.per_account_charge),
            'submission_count': count,
            'commission': commission,
        })

    return JsonResponse({
        'success': True,
        'employee_name': employee.name,
        'banks': data,
        'total_commission': round(total_commission, 2),
    })


@login_required
@user_passes_test(superuser_required, login_url='/report/')
@require_http_methods(["POST"])
def save_submissions(request):
    """Save bank submission counts for an employee for a month."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    emp_type = data.get('emp_type')
    employee_id = data.get('employee_id')
    year = data.get('year')
    month = data.get('month')
    submissions = data.get('submissions', {})  # {bank_id: count}

    if not all([emp_type, employee_id, year, month]):
        return JsonResponse({'success': False, 'error': 'Missing required fields'}, status=400)

    if emp_type == 'inhouse':
        try:
            employee = Employee.objects.get(id=employee_id)
        except Employee.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        fk_kwargs = {'employee': employee}
    else:
        try:
            employee = RemoteEmployee.objects.get(id=employee_id)
        except RemoteEmployee.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        fk_kwargs = {'remote_employee': employee}

    try:
        year = int(year)
        month = int(month)
    except (ValueError, TypeError):
        return JsonResponse({'success': False, 'error': 'Invalid year/month'}, status=400)

    total_commission = 0.0
    for bank_id_str, count_val in submissions.items():
        try:
            bank_id = int(bank_id_str)
            count = int(count_val)
            bank = Bank.objects.get(id=bank_id, is_active=True)
        except (ValueError, TypeError, Bank.DoesNotExist):
            continue

        if count <= 0:
            BankSubmission.objects.filter(bank=bank, year=year, month=month, **fk_kwargs).delete()
        else:
            obj, _ = BankSubmission.objects.update_or_create(
                bank=bank, year=year, month=month, **fk_kwargs,
                defaults={'submission_count': count},
            )
            total_commission += float(obj.submission_count * bank.per_account_charge)

    logger.info("Bank submissions saved for %s by %s", employee.name, request.user.username)
    return JsonResponse({'success': True, 'total_commission': round(total_commission, 2)})


# ============================================
# API: In-house Employee Adjustments
# ============================================

@login_required
@user_passes_test(superuser_required, login_url='/report/')
def get_adjustments(request, employee_id):
    """Get adjustments for an in-house employee for a specific month."""
    try:
        year = int(request.GET.get('year', datetime.datetime.now().year))
        month = int(request.GET.get('month', datetime.datetime.now().month))
    except (ValueError, TypeError):
        return JsonResponse({'success': False, 'error': 'Invalid year/month'})

    try:
        employee = Employee.objects.get(id=employee_id)
    except Employee.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)

    adjustments = PayrollAdjustment.objects.filter(
        employee=employee, year=year, month=month
    )
    data = [{
        'id': adj.id,
        'type': adj.adjustment_type,
        'amount': float(adj.amount),
        'reason': adj.reason,
        'created_at': adj.created_at.strftime('%Y-%m-%d %H:%M'),
    } for adj in adjustments]

    return JsonResponse({
        'success': True,
        'employee_name': employee.name,
        'adjustments': data,
    })


@login_required
@user_passes_test(superuser_required, login_url='/report/')
@require_http_methods(["POST"])
def add_adjustment(request):
    """Add a new adjustment for an in-house employee."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    employee_id = data.get('employee_id')
    year = data.get('year')
    month = data.get('month')
    adjustment_type = data.get('type')
    amount = data.get('amount')
    reason = data.get('reason', '')

    if not all([employee_id, year, month, adjustment_type, amount]):
        return JsonResponse({'success': False, 'error': 'Missing required fields'}, status=400)

    if adjustment_type not in ('incentive', 'reduction'):
        return JsonResponse({'success': False, 'error': 'Invalid adjustment type'}, status=400)

    try:
        employee = Employee.objects.get(id=employee_id)
    except Employee.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)

    try:
        adjustment = PayrollAdjustment.objects.create(
            employee=employee,
            year=int(year),
            month=int(month),
            adjustment_type=adjustment_type,
            amount=Decimal(str(amount)),
            reason=reason,
        )
    except (ValueError, TypeError) as e:
        return JsonResponse({'success': False, 'error': f'Invalid data: {e}'}, status=400)

    logger.info(
        "Payroll adjustment added: %s %s %s by %s",
        employee.name, adjustment_type, amount, request.user.username
    )
    return JsonResponse({
        'success': True,
        'message': 'Adjustment added successfully',
        'adjustment': {
            'id': adjustment.id,
            'type': adjustment.adjustment_type,
            'amount': float(adjustment.amount),
            'reason': adjustment.reason,
        },
    })


# ============================================
# API: Remote Employee Adjustments
# ============================================

@login_required
@user_passes_test(superuser_required, login_url='/report/')
def get_remote_adjustments(request, employee_id):
    """Get adjustments for a remote employee for a specific month."""
    try:
        year = int(request.GET.get('year', datetime.datetime.now().year))
        month = int(request.GET.get('month', datetime.datetime.now().month))
    except (ValueError, TypeError):
        return JsonResponse({'success': False, 'error': 'Invalid year/month'})

    try:
        employee = RemoteEmployee.objects.get(id=employee_id)
    except RemoteEmployee.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)

    adjustments = PayrollAdjustment.objects.filter(
        remote_employee=employee, year=year, month=month
    )
    data = [{
        'id': adj.id,
        'type': adj.adjustment_type,
        'amount': float(adj.amount),
        'reason': adj.reason,
        'created_at': adj.created_at.strftime('%Y-%m-%d %H:%M'),
    } for adj in adjustments]

    return JsonResponse({
        'success': True,
        'employee_name': employee.name,
        'adjustments': data,
    })


@login_required
@user_passes_test(superuser_required, login_url='/report/')
@require_http_methods(["POST"])
def add_remote_adjustment(request):
    """Add a new adjustment for a remote employee."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    employee_id = data.get('employee_id')
    year = data.get('year')
    month = data.get('month')
    adjustment_type = data.get('type')
    amount = data.get('amount')
    reason = data.get('reason', '')

    if not all([employee_id, year, month, adjustment_type, amount]):
        return JsonResponse({'success': False, 'error': 'Missing required fields'}, status=400)

    if adjustment_type not in ('incentive', 'reduction'):
        return JsonResponse({'success': False, 'error': 'Invalid adjustment type'}, status=400)

    try:
        employee = RemoteEmployee.objects.get(id=employee_id)
    except RemoteEmployee.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)

    try:
        adjustment = PayrollAdjustment.objects.create(
            remote_employee=employee,
            year=int(year),
            month=int(month),
            adjustment_type=adjustment_type,
            amount=Decimal(str(amount)),
            reason=reason,
        )
    except (ValueError, TypeError) as e:
        return JsonResponse({'success': False, 'error': f'Invalid data: {e}'}, status=400)

    logger.info(
        "Remote payroll adjustment added: %s %s %s by %s",
        employee.name, adjustment_type, amount, request.user.username
    )
    return JsonResponse({
        'success': True,
        'message': 'Adjustment added successfully',
        'adjustment': {
            'id': adjustment.id,
            'type': adjustment.adjustment_type,
            'amount': float(adjustment.amount),
            'reason': adjustment.reason,
        },
    })


# ============================================
# API: Recalculate Summaries
# ============================================

@login_required
@user_passes_test(superuser_required, login_url='/report/')
@require_http_methods(["POST"])
def recalculate_summaries(request):
    """Trigger recalculation of monthly summaries for the selected month/year."""
    try:
        data = json.loads(request.body)
        year = int(data.get('year'))
        month = int(data.get('month'))
    except (json.JSONDecodeError, TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Invalid year/month'}, status=400)

    if not (1 <= month <= 12) or not (2000 <= year <= 2099):
        return JsonResponse({'success': False, 'error': 'Invalid year/month'}, status=400)

    call_command('recalculate_summaries', year, month, verbosity=0)
    logger.info("Payroll summaries recalculated for %s/%s by %s", year, month, request.user.username)
    return JsonResponse({'success': True})


# ============================================
# API: Delete Adjustment (both types)
# ============================================

# ============================================
# Upload: Bank Submissions XLSX
# ============================================

@login_required
@user_passes_test(superuser_required, login_url='/report/')
@require_http_methods(["POST"])
def upload_submissions(request):
    """
    Parse a Target vs Achieved XLSX file and upsert BankSubmission records.

    Expected columns (any order, header detection by keyword):
      - Id      → employee TCR ID (optional; used when present)
      - Agent   → employee name (fallback lookup when Id is absent/empty)
      - {BankName} Ach → achieved submission count for each bank

    Each row = one employee. The achieved count for each bank is read directly
    from the "{Bank} Ach" column (Total Ach is ignored).
    Employees are looked up by tcr_id (via Id column) or name (via Agent column)
    across both Employee and RemoteEmployee.
    """
    uploaded_file = request.FILES.get('file')
    if not uploaded_file:
        return JsonResponse({'success': False, 'error': 'No file provided'}, status=400)

    try:
        year = int(request.POST.get('year'))
        month = int(request.POST.get('month'))
    except (TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Invalid year/month'}, status=400)

    if not (1 <= month <= 12) or not (2000 <= year <= 2099):
        return JsonResponse({'success': False, 'error': 'Invalid year/month range'}, status=400)

    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(uploaded_file.read()), read_only=True, data_only=True)
        ws = wb.active
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Could not read file: {e}'}, status=400)

    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return JsonResponse({'success': False, 'error': 'File is empty'}, status=400)

    # Detect columns from header row
    raw_headers = [str(c).strip() if c else '' for c in rows[0]]
    headers_lower = [h.lower() for h in raw_headers]

    agent_col = id_col = None
    # {bank_name_lower: col_index} for "{Bank} Ach" columns (excluding "total ach")
    bank_ach_cols = {}

    for i, h in enumerate(headers_lower):
        if h == 'id':
            id_col = i
        elif h == 'agent':
            agent_col = i
        elif h.endswith(' ach') and not h.startswith('total'):
            bank_name = raw_headers[i][:-4].strip()  # strip " Ach" suffix
            bank_ach_cols[bank_name.lower()] = (i, bank_name)

    if agent_col is None:
        return JsonResponse(
            {'success': False, 'error': 'Could not find "Agent" column in header'},
            status=400
        )
    if not bank_ach_cols:
        return JsonResponse(
            {'success': False, 'error': 'Could not find any "{Bank} Ach" columns in header'},
            status=400
        )

    # Preload active banks for fast lookup (case-insensitive)
    bank_map = {b.name.lower(): b for b in Bank.objects.filter(is_active=True)}

    matched = 0
    unmatched_agents = set()
    unmatched_banks = set()

    for row in rows[1:]:
        if not any(row):
            continue

        agent_name = str(row[agent_col]).strip() if row[agent_col] else ''
        tcr_id = str(row[id_col]).strip() if (id_col is not None and row[id_col]) else ''

        if not agent_name and not tcr_id:
            continue

        # Employee lookup: prefer tcr_id, fall back to name
        employee = remote_employee = None
        if tcr_id:
            employee = Employee.objects.filter(tcr_id=tcr_id, is_active=True).first()
            if not employee:
                remote_employee = RemoteEmployee.objects.filter(tcr_id=tcr_id, is_active=True).first()
        if not employee and not remote_employee and agent_name:
            employee = Employee.objects.filter(name__iexact=agent_name, is_active=True).first()
            if not employee:
                remote_employee = RemoteEmployee.objects.filter(name__iexact=agent_name, is_active=True).first()

        if not employee and not remote_employee:
            unmatched_agents.add(tcr_id or agent_name)
            continue

        fk_kwargs = {'employee': employee} if employee else {'remote_employee': remote_employee}

        for bank_lower, (col_idx, bank_display_name) in bank_ach_cols.items():
            try:
                count = int(row[col_idx]) if row[col_idx] is not None else 0
            except (ValueError, TypeError):
                count = 0

            bank = bank_map.get(bank_lower)
            if not bank:
                unmatched_banks.add(bank_display_name)
                continue

            if count <= 0:
                BankSubmission.objects.filter(bank=bank, year=year, month=month, **fk_kwargs).delete()
            else:
                BankSubmission.objects.update_or_create(
                    bank=bank, year=year, month=month, **fk_kwargs,
                    defaults={'submission_count': count},
                )
            matched += 1

    logger.info(
        "Submission upload for %d/%d: %d saved, %d unmatched agents, %d unmatched banks by %s",
        year, month, matched, len(unmatched_agents), len(unmatched_banks), request.user.username,
    )

    return JsonResponse({
        'success': True,
        'stats': {
            'matched': matched,
            'unmatched_agents': sorted(unmatched_agents),
            'unmatched_banks': sorted(unmatched_banks),
        },
    })


@login_required
@user_passes_test(superuser_required, login_url='/report/')
@require_http_methods(["POST"])
def delete_adjustment(request, adjustment_id):
    """Delete an adjustment (works for both in-house and remote)."""
    try:
        adjustment = PayrollAdjustment.objects.get(id=adjustment_id)
    except PayrollAdjustment.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Adjustment not found'}, status=404)

    logger.info(
        "Payroll adjustment deleted: id=%s by %s",
        adjustment_id, request.user.username
    )
    adjustment.delete()
    return JsonResponse({'success': True, 'message': 'Adjustment deleted'})
