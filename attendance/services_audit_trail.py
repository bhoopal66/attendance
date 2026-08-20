"""Cross-model audit trail for one employee.

THE PROBLEM THIS SOLVES
------------------------
log_audit(actor, action, instance, ...) stamps app_label/model_name/object_id
from the INSTANCE PASSED IN, not from the employee. A salary revision audits
the SalaryStructure row's own pk; an employer-cost save audits the
EmployerCostSetup row's pk; a recoverable audits the Recoverable row's pk.
Only compliance_reveal/_save_compliance audit the Employee row directly. So
"everything that happened to this employee" cannot be a single
AuditLog.objects.filter(object_id=employee.pk) — it has to gather every
child row this employee owns across each model, then query by
(app_label, model_name, object_id) per model.

WHAT IS DELIBERATELY EXCLUDED
------------------------------
PayrollRun.advance() writes audit rows scoped to a whole calendar month, not
one employee — there is no way to know which employees a given run affected
without guessing, so those rows never appear here.
"""
import logging

logger = logging.getLogger('attendance')


def employee_audit_trail(employee):
    """AuditLog rows for this employee and everything that belongs to them,
    newest first."""
    from django.db.models import Q

    from .models import (
        AuditLog, EmployeeAsset, EmployeeReturnToWork, EmployeeVisa, EmployeeWarning,
    )

    q = Q(app_label='attendance', model_name='employee', object_id=str(employee.pk))

    child_models = [
        ('attendance', 'salarystructure',
         employee.salary_structures.values_list('id', flat=True)),
        ('attendance', 'employercostsetup',
         employee.cost_setups.values_list('id', flat=True)),
        ('attendance', 'recoverable',
         employee.recoverables.values_list('id', flat=True)),
        ('attendance', 'employeedocument',
         employee.documents.values_list('id', flat=True)),
        ('attendance', 'employeevisa',
         EmployeeVisa.objects.filter(employee=employee).values_list('id', flat=True)),
        ('attendance', 'employeereturntowork',
         EmployeeReturnToWork.objects.filter(employee=employee).values_list('id', flat=True)),
        ('attendance', 'employeewarning',
         EmployeeWarning.objects.filter(employee=employee).values_list('id', flat=True)),
        ('attendance', 'employeeasset',
         EmployeeAsset.objects.filter(employee=employee).values_list('id', flat=True)),
    ]
    for app_label, model_name, id_qs in child_models:
        ids = [str(i) for i in id_qs]
        if ids:
            q |= Q(app_label=app_label, model_name=model_name, object_id__in=ids)

    try:
        from payroll.models import DeductionEntry
        deduction_ids = [str(i) for i in
                         DeductionEntry.objects.filter(employee=employee).values_list('id', flat=True)]
        if deduction_ids:
            q |= Q(app_label='payroll', model_name='deductionentry', object_id__in=deduction_ids)
    except Exception:
        logger.exception('Could not include deduction entries in audit trail for employee %s', employee.pk)

    return AuditLog.objects.filter(q).order_by('-timestamp')
