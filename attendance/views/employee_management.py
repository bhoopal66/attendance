"""
Employee management views for admin users.
Custom interface for managing all employees without Django admin.
"""

import json
import logging

from django.http import JsonResponse
from django.shortcuts import render
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.hashers import make_password

from ..models import Employee, RemoteEmployee
from .utils import superuser_required

logger = logging.getLogger('attendance')

# Fields that are safe to update via the API
ALLOWED_UPDATE_FIELDS = {
    'name', 'email', 'phone', 'department', 'location', 'team',
    'is_active', 'salary', 'designation', 'joining_date', 'leaving_date',
}
ALLOWED_BULK_FIELDS = {'department', 'location', 'team', 'is_active'}


def _serialize_employee(emp, emp_type):
    """Serialize an employee (in-house or remote) to a dict."""
    data = {
        'id': emp.id,
        'type': emp_type,
        'identifier': emp.person_id if emp_type == 'inhouse' else emp.extension_id,
        'name': emp.name,
        'email': emp.email or '',
        'phone': emp.phone or '',
        'department': emp.department or '',
        'location': emp.location or '',
        'team': emp.team or '',
        'is_active': emp.is_active,
        'joining_date': emp.joining_date.strftime('%Y-%m-%d') if emp.joining_date else '',
        'leaving_date': emp.leaving_date.strftime('%Y-%m-%d') if emp.leaving_date else '',
    }
    data['salary'] = float(emp.salary) if emp.salary else None
    data['designation'] = emp.designation if emp_type == 'remote' else None
    return data


@login_required
@user_passes_test(superuser_required, login_url='/report/')
def employee_management(request):
    """Display all employees (in-house and remote) in a unified management page."""
    inhouse_employees = Employee.objects.all().order_by('name')
    remote_employees = RemoteEmployee.objects.all().order_by('name')

    all_employees = []
    for emp in inhouse_employees:
        all_employees.append(_serialize_employee(emp, 'inhouse'))
    for emp in remote_employees:
        all_employees.append(_serialize_employee(emp, 'remote'))

    all_employees.sort(key=lambda x: x['name'].lower())

    departments = sorted(set(e['department'] for e in all_employees if e['department']))
    locations = sorted(set(e['location'] for e in all_employees if e['location']))
    teams = sorted(set(e['team'] for e in all_employees if e['team']))

    context = {
        'employees': all_employees,
        'departments': departments,
        'locations': locations,
        'teams': teams,
        'total_count': len(all_employees),
        'inhouse_count': inhouse_employees.count(),
        'remote_count': remote_employees.count(),
    }

    return render(request, 'attendance/employee_management.html', context)


def _get_employee_by_type(employee_id, employee_type):
    """Look up an employee by ID and type. Returns employee or None."""
    try:
        if employee_type == 'inhouse':
            return Employee.objects.get(id=employee_id)
        else:
            return RemoteEmployee.objects.get(id=employee_id)
    except (Employee.DoesNotExist, RemoteEmployee.DoesNotExist):
        return None


@login_required
@user_passes_test(superuser_required, login_url='/report/')
def update_employee(request):
    """API endpoint to update employee data."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    employee_id = data.get('id')
    employee_type = data.get('type')

    if not employee_id or not employee_type:
        return JsonResponse({'success': False, 'error': 'Missing id or type'}, status=400)

    emp = _get_employee_by_type(employee_id, employee_type)
    if not emp:
        return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)

    # Update allowed fields
    for field in ALLOWED_UPDATE_FIELDS:
        if field not in data:
            continue
        # designation only exists on RemoteEmployee
        if field == 'designation' and not hasattr(emp, 'designation'):
            continue
        value = data[field]
        if field in ('email', 'phone', 'department', 'location', 'team', 'designation', 'joining_date', 'leaving_date'):
            value = value or None
        setattr(emp, field, value)

    # Handle password separately (needs hashing)
    if data.get('portal_password'):
        emp.portal_password = make_password(data['portal_password'])

    try:
        emp.save()
        logger.info("Employee updated: %s (id=%s) by %s", emp.name, emp.id, request.user.username)
        return JsonResponse({'success': True, 'message': 'Employee updated successfully'})
    except Exception:
        logger.exception("Error updating employee id=%s", employee_id)
        return JsonResponse({'success': False, 'error': 'Failed to update employee.'}, status=500)


@login_required
@user_passes_test(superuser_required, login_url='/report/')
def bulk_update_employees(request):
    """API endpoint to bulk update employee fields."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    employee_ids = data.get('employees', [])
    updates = data.get('updates', {})

    if not employee_ids:
        return JsonResponse({'success': False, 'error': 'No employees selected'})

    # Only allow safe fields for bulk update
    safe_updates = {k: v for k, v in updates.items() if k in ALLOWED_BULK_FIELDS}
    if not safe_updates:
        return JsonResponse({'success': False, 'error': 'No valid fields to update'})

    updated_count = 0
    for emp_info in employee_ids:
        emp = _get_employee_by_type(emp_info.get('id'), emp_info.get('type'))
        if not emp:
            continue

        for field, value in safe_updates.items():
            if field in ('department', 'location', 'team'):
                setattr(emp, field, value or None)
            elif field == 'is_active':
                emp.is_active = value

        emp.save()
        updated_count += 1

    logger.info("Bulk update: %d employees updated by %s", updated_count, request.user.username)
    return JsonResponse({
        'success': True,
        'message': f'Updated {updated_count} employees'
    })
