"""
Payroll calculation views.
"""

import json
import datetime
import calendar
import logging
from decimal import Decimal

from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required, user_passes_test

from attendance.models import (
    Employee, RemoteEmployee, Holiday,
    LeaveRequest, MonthlySummary, RemoteMonthlySummary,
)
from attendance.views.utils import (
    MONTH_CHOICES, MONTH_NAMES, YEAR_RANGE,
    get_selected_month_year, superuser_required,
)
from .models import PayrollAdjustment

logger = logging.getLogger('payroll')


# ============================================
# Payroll Calculation Helpers
# ============================================

def _count_holidays(year, month, days_in_month):
    """Return total non-working days (Sundays + custom holidays) for a month."""
    sundays = sum(
        1 for day in range(1, days_in_month + 1)
        if datetime.date(year, month, day).weekday() == 6
    )
    custom = Holiday.objects.filter(date__year=year, date__month=month).count()
    return sundays + custom


def _get_inhouse_payroll_row(emp, year, month, month_start, month_end, total_holidays):
    """Build a payroll data row for one in-house employee."""
    summary = MonthlySummary.objects.filter(
        employee=emp, year=year, month=month
    ).first()

    if summary:
        half_days = summary.half_days or 0
        full_days = summary.working_days - half_days
        effective_work_days = full_days + (half_days * 0.5)
    else:
        full_days = 0
        half_days = 0
        effective_work_days = 0.0

    salary = float(emp.salary) if emp.salary else 0.0

    approved_leaves = LeaveRequest.objects.filter(
        employee=emp,
        status='approved',
        start_date__lte=month_end,
        end_date__gte=month_start,
    )
    paid_leave_days = sum(leave.get_effective_days() for leave in approved_leaves)

    daily_rate = salary / 30 if salary > 0 else 0.0
    total_working_days = effective_work_days + total_holidays + paid_leave_days
    base_payroll = daily_rate * total_working_days

    adjustments = PayrollAdjustment.objects.filter(employee=emp, year=year, month=month)
    incentives = float(
        adjustments.filter(adjustment_type='incentive').aggregate(total=Sum('amount'))['total'] or 0
    )
    reductions = float(
        adjustments.filter(adjustment_type='reduction').aggregate(total=Sum('amount'))['total'] or 0
    )
    net_payroll = base_payroll + incentives - reductions

    return {
        'employee': emp,
        'employee_type': 'inhouse',
        'salary': salary,
        'full_days': full_days,
        'half_days': half_days,
        'effective_work_days': round(effective_work_days, 1),
        'holidays': total_holidays,
        'paid_leave_days': paid_leave_days,
        'total_working_days': round(total_working_days, 1),
        'daily_rate': round(daily_rate, 2),
        'base_payroll': round(base_payroll, 2),
        'incentives': round(incentives, 2),
        'reductions': round(reductions, 2),
        'net_payroll': round(net_payroll, 2),
    }


def _get_remote_payroll_row(emp, year, month, total_holidays):
    """Build a payroll data row for one remote employee."""
    summary = RemoteMonthlySummary.objects.filter(
        employee=emp, year=year, month=month
    ).first()

    if summary:
        present_days = summary.present_days or 0
        half_days = summary.half_days or 0
    else:
        present_days = 0
        half_days = 0

    salary = float(emp.salary) if emp.salary else 0.0
    effective_work_days = present_days + (half_days * 0.5)
    daily_rate = salary / 30 if salary > 0 else 0.0
    total_working_days = effective_work_days + total_holidays
    base_payroll = daily_rate * total_working_days

    adjustments = PayrollAdjustment.objects.filter(remote_employee=emp, year=year, month=month)
    incentives = float(
        adjustments.filter(adjustment_type='incentive').aggregate(total=Sum('amount'))['total'] or 0
    )
    reductions = float(
        adjustments.filter(adjustment_type='reduction').aggregate(total=Sum('amount'))['total'] or 0
    )
    net_payroll = base_payroll + incentives - reductions

    return {
        'employee': emp,
        'employee_type': 'remote',
        'salary': salary,
        'present_days': present_days,
        'half_days': half_days,
        'effective_work_days': round(effective_work_days, 1),
        'holidays': total_holidays,
        'total_working_days': round(total_working_days, 1),
        'daily_rate': round(daily_rate, 2),
        'base_payroll': round(base_payroll, 2),
        'incentives': round(incentives, 2),
        'reductions': round(reductions, 2),
        'net_payroll': round(net_payroll, 2),
    }


def _build_section_totals(rows):
    """Sum net, incentives, reductions across a list of payroll rows."""
    return (
        round(sum(r['net_payroll'] for r in rows), 2),
        round(sum(r['incentives'] for r in rows), 2),
        round(sum(r['reductions'] for r in rows), 2),
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

    # --- Admin section (in-house Admin dept) ---
    admin_employees = Employee.objects.filter(
        department='Admin', is_active=True
    ).order_by('name')
    admin_data = [
        _get_inhouse_payroll_row(emp, selected_year, selected_month, month_start, month_end, total_holidays)
        for emp in admin_employees
    ]
    total_admin, admin_incentives_total, admin_reductions_total = _build_section_totals(admin_data)

    # --- Sales section: in-house Sales employees ---
    sales_inhouse_employees = Employee.objects.filter(
        department='Sales', is_active=True
    ).order_by('name')
    sales_inhouse_data = [
        _get_inhouse_payroll_row(emp, selected_year, selected_month, month_start, month_end, total_holidays)
        for emp in sales_inhouse_employees
    ]
    total_sales_inhouse, _, _ = _build_section_totals(sales_inhouse_data)

    # --- Sales section: remote employees ---
    remote_employees = RemoteEmployee.objects.filter(is_active=True).order_by('name')
    remote_data = [
        _get_remote_payroll_row(emp, selected_year, selected_month, total_holidays)
        for emp in remote_employees
    ]
    total_remote, _, _ = _build_section_totals(remote_data)

    # Combined Sales totals
    all_sales_rows = sales_inhouse_data + remote_data
    total_sales, sales_incentives_total, sales_reductions_total = _build_section_totals(all_sales_rows)

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
        # Sales
        'sales_inhouse_data': sales_inhouse_data,
        'total_sales_inhouse': total_sales_inhouse,
        'remote_data': remote_data,
        'total_remote': total_remote,
        'total_sales': total_sales,
        'sales_incentives_total': sales_incentives_total,
        'sales_reductions_total': sales_reductions_total,
        # Grand total
        'grand_total': grand_total,
    }

    return render(request, 'payroll/dashboard.html', context)


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
# API: Delete Adjustment (both types)
# ============================================

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
