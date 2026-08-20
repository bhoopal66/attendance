"""
Salary cycle history — payroll-side surface.

Thin, gated wrappers around `attendance.services_salary_cycle` so the
Payroll → Employees page can manage pay-cycle history without depending on
the `'employees'` section grant (attendance's own copy of these endpoints,
in `attendance/views/employee_management.py`, is gated `'employees'`
instead — the same field is already editable from both apps' pages today).

The company-wide default timeline ("for everyone") only lives here — it is
not per-employee, so it does not need an attendance-side counterpart.

Access: the 'payroll' section grant, same as the rest of this page.
"""

import datetime
import json
import logging

from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from attendance.views.utils import section_required

logger = logging.getLogger('payroll')


def _serialize_cycle_row(row):
    return {
        'id': row.id,
        'cycle_start_day': row.cycle_start_day,
        'effective_date': row.effective_date.isoformat(),
        'note': row.note,
        'created_by': row.created_by,
        'created_at': row.created_at.strftime('%Y-%m-%d %H:%M'),
    }


def _get_employee(emp_type, employee_id):
    from attendance.models import Employee, RemoteEmployee
    try:
        if emp_type == 'inhouse':
            return Employee.objects.get(id=employee_id)
        return RemoteEmployee.objects.get(id=employee_id)
    except (Employee.DoesNotExist, RemoteEmployee.DoesNotExist):
        return None


def _group_context(emp):
    """(group_key_or_None, group_label_or_None, group_timeline_rows)."""
    from payroll.services_payroll_engine import SECTION_LABELS, classify_employee_section
    from attendance.services_salary_cycle import group_cycle_timeline

    group_key = classify_employee_section(emp)
    if not group_key:
        return None, None, []
    return group_key, SECTION_LABELS.get(group_key, group_key), group_cycle_timeline(group_key)


@login_required
@user_passes_test(section_required('payroll'), login_url='/report/')
def employee_salary_cycle_history(request, emp_type, employee_id):
    """Current pay cycle + this employee's override/group/company timelines."""
    from attendance.services_salary_cycle import default_cycle_timeline, employee_cycle_timeline

    emp = _get_employee(emp_type, employee_id)
    if not emp:
        return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)

    group_key, group_label, group_rows = _group_context(emp)

    return JsonResponse({
        'success': True,
        'current_cycle_start_day': emp.salary_cycle_start_day,
        'group': {'key': group_key, 'label': group_label} if group_key else None,
        'history': [_serialize_cycle_row(r) for r in employee_cycle_timeline(emp)],
        'group_history': [_serialize_cycle_row(r) for r in group_rows],
        'defaults': [_serialize_cycle_row(r) for r in default_cycle_timeline()],
    })


@login_required
@user_passes_test(section_required('payroll'), login_url='/report/')
@require_http_methods(["POST"])
def employee_salary_cycle_history_add(request, emp_type, employee_id):
    """Add (or correct) one dated pay-cycle override for this employee."""
    from attendance.services_salary_cycle import employee_cycle_timeline, set_employee_cycle_override

    emp = _get_employee(emp_type, employee_id)
    if not emp:
        return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    eff_date = data.get('effective_date') or datetime.date.today().isoformat()
    try:
        obj = set_employee_cycle_override(
            emp,
            data.get('cycle_start_day'),
            eff_date,
            actor=request.user.username,
            note=data.get('note', ''),
            allow_noop_skip=False,
        )
    except ValidationError as e:
        return JsonResponse({'success': False, 'error': '; '.join(e.messages)}, status=400)

    return JsonResponse({
        'success': True,
        'warning': getattr(obj, 'warning', None),
        'current_cycle_start_day': emp.salary_cycle_start_day,
        'history': [_serialize_cycle_row(r) for r in employee_cycle_timeline(emp)],
    })


@login_required
@user_passes_test(section_required('payroll'), login_url='/report/')
@require_http_methods(["POST"])
def employee_salary_cycle_history_delete(request, emp_type, employee_id, history_id):
    """Undo the most recent pay-cycle override for this employee only."""
    from attendance.services_salary_cycle import delete_latest_employee_override, employee_cycle_timeline

    emp = _get_employee(emp_type, employee_id)
    if not emp:
        return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)

    try:
        delete_latest_employee_override(emp, history_id, actor=request.user.username)
    except ValidationError as e:
        return JsonResponse({'success': False, 'error': '; '.join(e.messages)}, status=400)

    return JsonResponse({
        'success': True,
        'current_cycle_start_day': emp.salary_cycle_start_day,
        'history': [_serialize_cycle_row(r) for r in employee_cycle_timeline(emp)],
    })


@login_required
@user_passes_test(section_required('payroll'), login_url='/report/')
def salary_cycle_default_list(request):
    """The company-wide default pay-cycle timeline."""
    from attendance.services_salary_cycle import current_default_cycle, default_cycle_timeline

    current = current_default_cycle()
    return JsonResponse({
        'success': True,
        'current': _serialize_cycle_row(current) if current else None,
        'defaults': [_serialize_cycle_row(r) for r in default_cycle_timeline()],
    })


@login_required
@user_passes_test(section_required('payroll'), login_url='/report/')
@require_http_methods(["POST"])
def salary_cycle_default_add(request):
    """Add (or correct) one dated entry on the company-wide default timeline."""
    from attendance.services_salary_cycle import default_cycle_timeline, set_default_cycle

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    eff_date = data.get('effective_date') or datetime.date.today().isoformat()
    try:
        obj = set_default_cycle(
            data.get('cycle_start_day'),
            eff_date,
            actor=request.user.username,
            note=data.get('note', ''),
        )
    except ValidationError as e:
        return JsonResponse({'success': False, 'error': '; '.join(e.messages)}, status=400)

    return JsonResponse({
        'success': True,
        'warning': getattr(obj, 'warning', None),
        'defaults': [_serialize_cycle_row(r) for r in default_cycle_timeline()],
    })


@login_required
@user_passes_test(section_required('payroll'), login_url='/report/')
@require_http_methods(["POST"])
def salary_cycle_default_delete(request, default_id):
    """Undo the most recent company-wide default entry only."""
    from attendance.services_salary_cycle import default_cycle_timeline, delete_latest_default

    try:
        delete_latest_default(default_id, actor=request.user.username)
    except ValidationError as e:
        return JsonResponse({'success': False, 'error': '; '.join(e.messages)}, status=400)

    return JsonResponse({
        'success': True,
        'defaults': [_serialize_cycle_row(r) for r in default_cycle_timeline()],
    })


# ------------------------------------------------------------------ page

@login_required
@user_passes_test(section_required('payroll'), login_url='/report/')
def pay_cycle_management(request):
    """The Pay Cycle Management page — Groups + Company Default tabs."""
    from payroll.services_payroll_engine import SECTION_LABELS, select_employees

    counts = select_employees()
    groups = [
        {'key': key, 'label': label, 'employee_count': len(counts.get(key, []))}
        for key, label in SECTION_LABELS.items()
    ]
    return render(request, 'payroll/pay_cycle_management.html', {'groups': groups})


# ---------------------------------------------------------------- groups

@login_required
@user_passes_test(section_required('payroll'), login_url='/report/')
def salary_cycle_groups_list(request):
    """All 4 groups' current cycle + timeline, in one response."""
    from payroll.services_payroll_engine import SECTION_LABELS
    from attendance.services_salary_cycle import current_group_cycle, group_cycle_timeline

    out = []
    for key, label in SECTION_LABELS.items():
        current = current_group_cycle(key)
        out.append({
            'key': key,
            'label': label,
            'current': _serialize_cycle_row(current) if current else None,
            'timeline': [_serialize_cycle_row(r) for r in group_cycle_timeline(key)],
        })
    return JsonResponse({'success': True, 'groups': out})


@login_required
@user_passes_test(section_required('payroll'), login_url='/report/')
@require_http_methods(["POST"])
def salary_cycle_group_add(request, group_key):
    """Add (or correct) one dated entry on one payroll group's timeline."""
    from attendance.services_salary_cycle import group_cycle_timeline, set_group_cycle

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    eff_date = data.get('effective_date') or datetime.date.today().isoformat()
    try:
        obj = set_group_cycle(
            group_key,
            data.get('cycle_start_day'),
            eff_date,
            actor=request.user.username,
            note=data.get('note', ''),
        )
    except ValidationError as e:
        return JsonResponse({'success': False, 'error': '; '.join(e.messages)}, status=400)

    return JsonResponse({
        'success': True,
        'warning': getattr(obj, 'warning', None),
        'timeline': [_serialize_cycle_row(r) for r in group_cycle_timeline(group_key)],
    })


@login_required
@user_passes_test(section_required('payroll'), login_url='/report/')
@require_http_methods(["POST"])
def salary_cycle_group_delete(request, group_key, entry_id):
    """Undo the most recent entry for one payroll group only."""
    from attendance.services_salary_cycle import delete_latest_group_default, group_cycle_timeline

    try:
        delete_latest_group_default(group_key, entry_id, actor=request.user.username)
    except ValidationError as e:
        return JsonResponse({'success': False, 'error': '; '.join(e.messages)}, status=400)

    return JsonResponse({
        'success': True,
        'timeline': [_serialize_cycle_row(r) for r in group_cycle_timeline(group_key)],
    })
