"""
TEMPORARY read-only diagnostic view — Phase D investigation.

Dumps the raw stored PaidSalaryRecord.snapshot JSON for a chosen employee
across a year range, unmodified, so we can see exactly what is stored
without recomputing anything. No writes, no schema changes.

URL: /payroll/api/debug/snapshot/?emp_type=inhouse&name=Abdul%20Rahman&from_year=2026&to_year=2026
Name: debug_snapshot

Delete this file (and its one url line) once the investigation is done —
it is not meant to be a permanent part of the app.
"""

from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from attendance.views.utils import section_required

from .models import PaidSalaryRecord


@login_required
@user_passes_test(section_required('payroll'), login_url='/report/')
@require_http_methods(['GET'])
def inspect_snapshot(request):
    """Read-only dump of raw PaidSalaryRecord rows/snapshots for one employee."""
    emp_type = request.GET.get('emp_type', 'inhouse')
    name = request.GET.get('name', '')
    from_year = request.GET.get('from_year')
    to_year = request.GET.get('to_year')

    qs = PaidSalaryRecord.objects.select_related('employee', 'remote_employee')
    if emp_type == 'inhouse':
        qs = qs.filter(employee__name__icontains=name)
    else:
        qs = qs.filter(remote_employee__name__icontains=name)
    if from_year:
        qs = qs.filter(year__gte=int(from_year))
    if to_year:
        qs = qs.filter(year__lte=int(to_year))

    out = []
    for r in qs.order_by('year', 'month'):
        emp = r.employee or r.remote_employee
        snap = r.snapshot or {}
        out.append({
            'record_id': r.id,
            'employee_name': emp.name if emp else None,
            'year': r.year,
            'month': r.month,
            'final_salary_field': str(r.final_salary),
            'paid_at': str(r.paid_at),
            'paid_by': r.paid_by,
            'snapshot_total_deductions': snap.get('total_deductions'),
            'snapshot_total_additions': snap.get('total_additions'),
            'snapshot_deductions_breakdown': snap.get('deductions_breakdown'),
            'snapshot_final_salary': snap.get('final_salary'),
            'snapshot_has_deductions_breakdown_key': 'deductions_breakdown' in snap,
            'snapshot_keys': sorted(snap.keys()),
        })

    return JsonResponse({'count': len(out), 'records': out}, json_dumps_params={'indent': 2})
