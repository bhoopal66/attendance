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

from attendance.models import Employee, Holiday, LeaveRequest, MonthlySummary
from attendance.views.utils import (
    MONTH_CHOICES, MONTH_NAMES, YEAR_RANGE,
    get_selected_month_year, superuser_required,
)
from .models import PayrollAdjustment

logger = logging.getLogger('payroll')


@login_required
@user_passes_test(superuser_required, login_url='/report/')
def payroll_dashboard(request):
    """
    Payroll dashboard showing Admin and Sales sections.

    Admin Payroll Formula:
    Base = (monthly_salary / 30) * working_days
    Net = Base + Incentives - Reductions
    """
    selected_month, selected_year = get_selected_month_year(request)

    _, days_in_month = calendar.monthrange(selected_year, selected_month)
    month_start = datetime.date(selected_year, selected_month, 1)
    month_end = datetime.date(selected_year, selected_month, days_in_month)

    sundays = sum(
        1 for day in range(1, days_in_month + 1)
        if datetime.date(selected_year, selected_month, day).weekday() == 6
    )
    holidays = Holiday.objects.filter(
        date__year=selected_year, date__month=selected_month
    ).count()
    total_holidays = sundays + holidays

    admin_employees = Employee.objects.filter(
        department='Admin', is_active=True
    ).order_by('name')

    admin_payroll_data = []
    total_admin_payroll = 0
    total_incentives = 0
    total_reductions = 0

    for emp in admin_employees:
        summary = MonthlySummary.objects.filter(
            employee=emp, year=selected_year, month=selected_month
        ).first()

        if summary:
            full_days = summary.working_days - (summary.half_days or 0)
            half_days = summary.half_days or 0
            working_days = full_days + (half_days * 0.5) + total_holidays
        else:
            full_days = 0
            half_days = 0
            working_days = total_holidays

        salary = float(emp.salary) if emp.salary else 0.0

        # Get approved paid leave days for this month (cross-month aware)
        approved_leaves = LeaveRequest.objects.filter(
            employee=emp,
            status='approved',
            start_date__lte=month_end,
            end_date__gte=month_start
        )
        paid_leave_days = sum(leave.get_effective_days() for leave in approved_leaves)

        daily_rate = salary / 30 if salary > 0 else 0.0
        total_working_days = working_days + paid_leave_days
        base_payroll = daily_rate * total_working_days

        adjustments = PayrollAdjustment.objects.filter(
            employee=emp, year=selected_year, month=selected_month
        )

        incentives = float(
            adjustments.filter(adjustment_type='incentive').aggregate(
                total=Sum('amount'))['total'] or 0
        )
        reductions = float(
            adjustments.filter(adjustment_type='reduction').aggregate(
                total=Sum('amount'))['total'] or 0
        )

        net_payroll = base_payroll + incentives - reductions

        admin_payroll_data.append({
            'employee': emp,
            'salary': salary,
            'working_days': total_working_days,
            'daily_rate': round(daily_rate, 2),
            'base_payroll': round(base_payroll, 2),
            'incentives': round(incentives, 2),
            'reductions': round(reductions, 2),
            'net_payroll': round(net_payroll, 2),
            'full_days': full_days,
            'half_days': half_days,
            'holidays': total_holidays,
            'paid_leave_days': paid_leave_days,
        })

        total_admin_payroll += net_payroll
        total_incentives += incentives
        total_reductions += reductions

    context = {
        'selected_month': selected_month,
        'selected_year': selected_year,
        'month_name': MONTH_NAMES[selected_month],
        'months': MONTH_CHOICES,
        'years': YEAR_RANGE,
        'admin_payroll_data': admin_payroll_data,
        'total_admin_payroll': round(total_admin_payroll, 2),
        'total_incentives': round(total_incentives, 2),
        'total_reductions': round(total_reductions, 2),
        'total_holidays': total_holidays,
    }

    return render(request, 'payroll/dashboard.html', context)


# ============================================
# API Endpoints for Adjustments
# ============================================

@login_required
@user_passes_test(superuser_required, login_url='/report/')
def get_adjustments(request, employee_id):
    """Get all adjustments for an employee for a specific month."""
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
        'created_at': adj.created_at.strftime('%Y-%m-%d %H:%M')
    } for adj in adjustments]

    return JsonResponse({
        'success': True,
        'employee_name': employee.name,
        'adjustments': data
    })


@login_required
@user_passes_test(superuser_required, login_url='/report/')
@require_http_methods(["POST"])
def add_adjustment(request):
    """Add a new adjustment for an employee."""
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
            reason=reason
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
            'reason': adjustment.reason
        }
    })


@login_required
@user_passes_test(superuser_required, login_url='/report/')
@require_http_methods(["POST"])
def delete_adjustment(request, adjustment_id):
    """Delete an adjustment."""
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
