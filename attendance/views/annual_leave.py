"""
Annual leave management views for admin.
Allows admins to assign annual leave to employees with paid/unpaid settings.
"""

import json
import logging
from datetime import date

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required, user_passes_test

from ..models import AnnualLeave, Employee, RemoteEmployee
from .utils import superuser_required, MONTH_CHOICES, YEAR_RANGE

logger = logging.getLogger('attendance')


@login_required
@user_passes_test(superuser_required)
def annual_leave_management(request):
    """Admin page to view and manage annual leaves."""
    today = date.today()
    year = int(request.GET.get('year', today.year))
    month = int(request.GET.get('month', 0))  # 0 = all months

    qs = AnnualLeave.objects.select_related('employee', 'remote_employee')

    if year:
        qs = qs.filter(start_date__year=year) | AnnualLeave.objects.filter(end_date__year=year).select_related('employee', 'remote_employee')
        qs = (
            AnnualLeave.objects
            .select_related('employee', 'remote_employee')
            .filter(start_date__lte=f'{year}-12-31', end_date__gte=f'{year}-01-01')
        )
    if month:
        import calendar
        days_in_month = calendar.monthrange(year, month)[1]
        month_start = date(year, month, 1)
        month_end = date(year, month, days_in_month)
        qs = qs.filter(start_date__lte=month_end, end_date__gte=month_start)

    annual_leaves = qs.order_by('-start_date')

    # Combined employee list for the "Add" modal — type encoded in each entry
    all_employees = sorted(
        [{'id': e['id'], 'name': e['name'], 'department': e['department'], 'type': 'inhouse'}
         for e in Employee.objects.filter(is_active=True).values('id', 'name', 'department')] +
        [{'id': e['id'], 'name': e['name'], 'department': e['department'], 'type': 'remote'}
         for e in RemoteEmployee.objects.filter(is_active=True).values('id', 'name', 'department')],
        key=lambda e: e['name'].lower()
    )

    context = {
        'annual_leaves': annual_leaves,
        'all_employees': json.dumps(all_employees),
        'selected_year': year,
        'selected_month': month,
        'year_range': YEAR_RANGE,
        'month_choices': MONTH_CHOICES,
        'total_count': annual_leaves.count(),
        'paid_count': annual_leaves.filter(is_paid=True).count(),
        'unpaid_count': annual_leaves.filter(is_paid=False).count(),
    }

    return render(request, 'attendance/annual_leave.html', context)


@login_required
@user_passes_test(superuser_required)
@require_http_methods(["POST"])
def add_annual_leave(request):
    """Add a new annual leave entry."""
    employee_type = request.POST.get('employee_type', 'inhouse')
    employee_id = request.POST.get('employee_id', '').strip()
    start_date_str = request.POST.get('start_date', '').strip()
    end_date_str = request.POST.get('end_date', '').strip()
    is_paid = request.POST.get('is_paid') == 'true'
    salary_percentage = request.POST.get('salary_percentage', '100').strip()
    reason = request.POST.get('reason', '').strip()
    admin_notes = request.POST.get('admin_notes', '').strip()

    if not employee_id or not start_date_str or not end_date_str:
        return JsonResponse({'success': False, 'error': 'Employee, start date, and end date are required.'}, status=400)

    try:
        from datetime import datetime
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({'success': False, 'error': 'Invalid date format.'}, status=400)

    if end_date < start_date:
        return JsonResponse({'success': False, 'error': 'End date cannot be before start date.'}, status=400)

    try:
        pct = float(salary_percentage)
        if not (0 <= pct <= 100):
            raise ValueError()
    except ValueError:
        return JsonResponse({'success': False, 'error': 'Salary percentage must be between 0 and 100.'}, status=400)

    employee = None
    remote_employee = None

    try:
        if employee_type == 'inhouse':
            employee = Employee.objects.get(id=employee_id)
        else:
            remote_employee = RemoteEmployee.objects.get(id=employee_id)
    except (Employee.DoesNotExist, RemoteEmployee.DoesNotExist):
        return JsonResponse({'success': False, 'error': 'Employee not found.'}, status=404)

    al = AnnualLeave.objects.create(
        employee=employee,
        remote_employee=remote_employee,
        start_date=start_date,
        end_date=end_date,
        is_paid=is_paid,
        salary_percentage=pct if is_paid else 0,
        reason=reason,
        admin_notes=admin_notes,
    )

    logger.info(
        "Annual leave added: id=%s employee=%s dates=%s-%s paid=%s pct=%s by %s",
        al.id, al.get_employee_name(), start_date, end_date, is_paid, pct,
        request.user.username
    )

    return JsonResponse({
        'success': True,
        'id': al.id,
        'employee_name': al.get_employee_name(),
        'start_date': str(al.start_date),
        'end_date': str(al.end_date),
        'days': al.get_days_count(),
        'is_paid': al.is_paid,
        'salary_percentage': float(al.salary_percentage),
        'reason': al.reason,
        'admin_notes': al.admin_notes,
    })


@login_required
@user_passes_test(superuser_required)
@require_http_methods(["POST"])
def delete_annual_leave(request, leave_id):
    """Delete an annual leave entry."""
    try:
        al = AnnualLeave.objects.get(id=leave_id)
    except AnnualLeave.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Annual leave not found.'}, status=404)

    name = al.get_employee_name()
    al.delete()

    logger.info("Annual leave deleted: id=%s employee=%s by %s", leave_id, name, request.user.username)

    return JsonResponse({'success': True, 'message': 'Annual leave deleted.'})
