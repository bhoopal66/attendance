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

from ..models import (
    AttendanceRecord, EarlyLeaveRequest, Employee, EmployeeIDAlias,
    LeaveRequest, MonthlySummary, RemoteCallRecord, RemoteEmployee,
    RemoteEmployeeIDAlias, RemoteMonthlySummary, ShiftHistory,
)
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
    if emp_type == 'inhouse':
        remote = getattr(emp, 'remote_employee', None)
        data['linked_remote_id'] = remote.id if remote else None
        data['linked_remote_name'] = remote.name if remote else None
    else:
        linked = emp.linked_employee
        data['linked_inhouse_id'] = linked.id if linked else None
        data['linked_inhouse_name'] = linked.name if linked else None
    return data


@login_required
@user_passes_test(superuser_required, login_url='/report/')
def employee_management(request):
    """Display all employees (in-house and remote) in a unified management page."""
    inhouse_employees = Employee.objects.select_related('remote_employee').all().order_by('name')
    remote_employees = RemoteEmployee.objects.select_related('linked_employee').all().order_by('name')

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


def _merge_inhouse_employees(keep, drop):
    """
    Transfer all records from drop → keep, then delete drop.
    On date conflicts the keep record takes priority (drop's record is discarded).
    """
    # Attendance records — skip dates already present on keep
    existing_dates = set(
        AttendanceRecord.objects.filter(employee=keep).values_list('date', flat=True)
    )
    AttendanceRecord.objects.filter(employee=drop).exclude(date__in=existing_dates).update(employee=keep)

    # Monthly summaries — skip months already present on keep
    existing_months = set(
        MonthlySummary.objects.filter(employee=keep).values_list('year', 'month')
    )
    for s in MonthlySummary.objects.filter(employee=drop):
        if (s.year, s.month) not in existing_months:
            s.employee = keep
            s.save(update_fields=['employee'])

    # Requests and shift history
    EarlyLeaveRequest.objects.filter(employee=drop).update(employee=keep)
    LeaveRequest.objects.filter(employee=drop).update(employee=keep)
    ShiftHistory.objects.filter(employee=drop).update(employee=keep)

    # Transfer existing aliases from drop to keep
    for alias in EmployeeIDAlias.objects.filter(employee=drop):
        EmployeeIDAlias.objects.get_or_create(employee=keep, person_id=alias.person_id)

    # Archive drop's current person_id so future uploads still resolve correctly
    EmployeeIDAlias.objects.get_or_create(employee=keep, person_id=drop.person_id)

    drop.delete()


def _merge_remote_employees(keep, drop):
    """
    Transfer all records from drop → keep, then delete drop.
    On date conflicts the keep record takes priority.
    """
    existing_dates = set(
        RemoteCallRecord.objects.filter(employee=keep).values_list('date', flat=True)
    )
    RemoteCallRecord.objects.filter(employee=drop).exclude(date__in=existing_dates).update(employee=keep)

    existing_months = set(
        RemoteMonthlySummary.objects.filter(employee=keep).values_list('year', 'month')
    )
    for s in RemoteMonthlySummary.objects.filter(employee=drop):
        if (s.year, s.month) not in existing_months:
            s.employee = keep
            s.save(update_fields=['employee'])

    EarlyLeaveRequest.objects.filter(remote_employee=drop).update(remote_employee=keep)

    for alias in RemoteEmployeeIDAlias.objects.filter(employee=drop):
        RemoteEmployeeIDAlias.objects.get_or_create(employee=keep, extension_id=alias.extension_id)

    RemoteEmployeeIDAlias.objects.get_or_create(employee=keep, extension_id=drop.extension_id)

    drop.delete()


@login_required
@user_passes_test(superuser_required, login_url='/report/')
def merge_employees(request):
    """
    Merge two employee records of the same type.
    All data from the dropped record is transferred to the kept record,
    then the dropped record is deleted.
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    keep_id = data.get('keep_id')
    drop_id = data.get('drop_id')
    emp_type = data.get('type')

    if not all([keep_id, drop_id, emp_type]):
        return JsonResponse({'success': False, 'error': 'Missing required fields'}, status=400)

    if keep_id == drop_id:
        return JsonResponse({'success': False, 'error': 'Cannot merge an employee with themselves'}, status=400)

    if emp_type not in ('inhouse', 'remote'):
        return JsonResponse({'success': False, 'error': 'Invalid employee type'}, status=400)

    try:
        if emp_type == 'inhouse':
            keep = Employee.objects.get(id=keep_id)
            drop = Employee.objects.get(id=drop_id)
            drop_name = drop.name
            _merge_inhouse_employees(keep, drop)
        else:
            keep = RemoteEmployee.objects.get(id=keep_id)
            drop = RemoteEmployee.objects.get(id=drop_id)
            drop_name = drop.name
            _merge_remote_employees(keep, drop)
    except (Employee.DoesNotExist, RemoteEmployee.DoesNotExist):
        return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
    except Exception:
        logger.exception("Error merging employees keep=%s drop=%s type=%s", keep_id, drop_id, emp_type)
        return JsonResponse({'success': False, 'error': 'Merge failed due to an unexpected error'}, status=500)

    logger.info(
        "Merged employee '%s' (id=%s) into '%s' (id=%s) [%s] by %s",
        drop_name, drop_id, keep.name, keep_id, emp_type, request.user.username
    )
    return JsonResponse({'success': True, 'message': f'Merged {drop_name} into {keep.name} successfully'})


@login_required
@user_passes_test(superuser_required, login_url='/report/')
def link_employees(request):
    """
    Link an in-house employee and a remote employee as the same person.
    Both records are preserved; this just marks them as representing the same individual.
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    inhouse_id = data.get('inhouse_id')
    remote_id = data.get('remote_id')

    if not inhouse_id or not remote_id:
        return JsonResponse({'success': False, 'error': 'Missing inhouse_id or remote_id'}, status=400)

    try:
        inhouse = Employee.objects.get(id=inhouse_id)
    except Employee.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'In-house employee not found'}, status=404)

    try:
        remote = RemoteEmployee.objects.get(id=remote_id)
    except RemoteEmployee.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Remote employee not found'}, status=404)

    existing_remote = getattr(inhouse, 'remote_employee', None)
    if existing_remote is not None:
        return JsonResponse(
            {'success': False, 'error': f'{inhouse.name} is already linked to {existing_remote.name}'},
            status=400
        )

    if remote.linked_employee is not None:
        return JsonResponse(
            {'success': False, 'error': f'{remote.name} is already linked to {remote.linked_employee.name}'},
            status=400
        )

    remote.linked_employee = inhouse
    remote.save(update_fields=['linked_employee'])

    logger.info(
        "Linked in-house '%s' (id=%s) with remote '%s' (id=%s) by %s",
        inhouse.name, inhouse.id, remote.name, remote.id, request.user.username
    )
    return JsonResponse({'success': True, 'message': f'Linked {inhouse.name} (In-House) with {remote.name} (Remote)'})


@login_required
@user_passes_test(superuser_required, login_url='/report/')
def unlink_employees(request):
    """Remove the link between an in-house and remote employee."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    remote_id = data.get('remote_id')
    if not remote_id:
        return JsonResponse({'success': False, 'error': 'Missing remote_id'}, status=400)

    try:
        remote = RemoteEmployee.objects.get(id=remote_id)
    except RemoteEmployee.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Remote employee not found'}, status=404)

    if remote.linked_employee is None:
        return JsonResponse({'success': False, 'error': 'Employee is not linked'}, status=400)

    inhouse_name = remote.linked_employee.name
    remote.linked_employee = None
    remote.save(update_fields=['linked_employee'])

    logger.info(
        "Unlinked remote '%s' (id=%s) from in-house '%s' by %s",
        remote.name, remote.id, inhouse_name, request.user.username
    )
    return JsonResponse({'success': True, 'message': f'Unlinked {remote.name} from {inhouse_name}'})
