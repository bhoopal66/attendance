"""
Employee Profile view — Phase 3 + Phase 4 + Phase 5 + Phase 6 + Phase 7 + Phase 8.

Provides a 360° read/edit view for a single in-house Employee.
Each section (personal, contact, identity, employment, salary, bank, cost,
document, recoverable, onboarding) submits independently via POST ?section=<name>.

Phase 4 addition:
  _save_employment and _save_bank now compare before/after values and
  create EmploymentHistory rows for every tracked field that changed.

Phase 5 addition:
  New _save_salary handler manages SalaryStructure revisions.
  Salary/currency fields removed from _save_bank (they now live in salary section).
  GET context includes current_salary and salary_history.

Phase 6 addition:
  New _save_cost handler manages EmployerCostSetup records (effective-dated).
  GET context includes current_cost and cost_history.

Phase 7 addition:
  New _save_document handler creates EmployeeDocument rows.
  GET context includes documents queryset (ordered by type then created_at).

Phase 8 addition:
  New _save_recoverable handler creates Recoverable sub-ledger rows.
  GET context includes recoverables queryset and type/status choices.

Phase 10 addition:
  GET context includes perf_trend (12-month target-vs-achieved series) and
  perf_current (latest month's performance row). Read-only on the profile —
  targets are edited on the Team Performance page or in Django admin.
"""

import json
import logging
from datetime import date

from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from django.utils import timezone

import datetime

from ..models import (
    AuditLog, Employee, EmploymentHistory, SalaryStructure, EmployerCostSetup,
    EmployeeDocument, Recoverable,
)
from ..audit import log_audit
from .. import compliance_access as access
from .. import services_compliance as svc_compliance
from ..services_compliance import build_block as build_compliance_block
from .utils import section_required

logger = logging.getLogger('attendance')


# Default onboarding checklist items — keys are stable IDs, values are labels
ONBOARDING_ITEMS = {
    'offer_letter':       'Offer letter signed',
    'id_documents':       'ID documents collected',
    'bank_details':       'Bank details captured',
    'it_access':          'IT access provisioned',
    'wps_registration':   'WPS / payroll registration',
    'eid_application':    'Emirates ID / visa application',
    'induction_complete': 'Induction / orientation done',
    'policy_ack':         'Company policy acknowledged',
}


@login_required
@user_passes_test(section_required('employees'), login_url='/report/')
def employee_profile(request, person_id):
    """
    GET  — render 360° profile page for the given in-house employee.
    POST — update the section specified by ?section= query param.
    """
    employee = get_object_or_404(Employee, person_id=person_id)

    if request.method == 'POST':
        return _handle_section_post(request, employee)

    # Build onboarding checklist as list of (key, label, is_done) tuples — no custom tag needed
    saved = employee.onboarding_checklist or {}
    checklist_rows = [
        (key, label, saved.get(key, False))
        for key, label in ONBOARDING_ITEMS.items()
    ]
    checklist_done = sum(1 for _, _, done in checklist_rows if done)

    # All active employees for reporting_manager dropdown (exclude self)
    managers = Employee.objects.filter(is_active=True).exclude(pk=employee.pk).order_by('name')

    # Employment history — newest first, all entries
    employment_history = employee.employment_history.select_related('employee').order_by(
        '-effective_date', '-changed_at'
    )

    # Salary structure — current approved revision + full history
    current_salary = (
        employee.salary_structures.filter(status='approved')
        .order_by('-effective_from', '-created_at')
        .first()
    )
    salary_history = employee.salary_structures.order_by('-effective_from', '-created_at')

    # Employer cost setup — most recent entry + full history
    current_cost = (
        employee.cost_setups
        .order_by('-effective_from', '-created_at')
        .first()
    )
    cost_history = employee.cost_setups.order_by('-effective_from', '-created_at')

    # Employee documents — all records, ordered by type then newest first
    documents = employee.documents.order_by('document_type', '-created_at')

    # Recoverables — all records, newest first; split by status for display
    recoverables = employee.recoverables.order_by('-created_at')

    # Phase 10 — performance trend (12 months ending this month).
    # Imported lazily to avoid a module-level attendance→payroll import cycle.
    today_d = date.today()
    try:
        from payroll.services_performance import person_trend
        perf_trend = person_trend(employee, 'inhouse', today_d.year, today_d.month, months=12)
        perf_current = perf_trend[-1] if perf_trend else None
        perf_max = max(
            (max(p['target'], p['achieved']) for p in perf_trend),
            default=0,
        )
        perf_has_data = any(p['target'] or p['achieved'] for p in perf_trend)
    except Exception:
        logger.exception('Performance trend unavailable for employee %s', employee.pk)
        perf_trend, perf_current, perf_max, perf_has_data = [], None, 0, False

    context = {
        'employee':           employee,
        'managers':           managers,
        'checklist_rows':     checklist_rows,
        'checklist_done':     checklist_done,
        'checklist_total':    len(ONBOARDING_ITEMS),
        'completeness':       employee.profile_completeness,
        'employment_history': employment_history,
        'current_salary':     current_salary,
        'salary_history':     salary_history,
        'current_cost':       current_cost,
        'cost_history':       cost_history,
        'documents':          documents,
        'document_type_choices': EmployeeDocument.DOCUMENT_TYPES,
        'recoverables':            recoverables,
        'recoverable_type_choices': Recoverable.RECOVERABLE_TYPES,
        'recoverable_status_choices': Recoverable.STATUS_CHOICES,
        # Phase 10 — performance
        'perf_trend':    perf_trend,
        'perf_current':  perf_current,
        'perf_max':      perf_max,
        'perf_has_data': perf_has_data,
        'perf_year':     today_d.year,
        'perf_month':    today_d.month,
        # Field choices exposed to template
        'gender_choices':       Employee.GENDER_CHOICES,
        'blood_group_choices':  Employee.BLOOD_GROUP_CHOICES,
        'status_choices':       Employee.EMPLOYMENT_STATUS_CHOICES,
        'currency_choices':     Employee._meta.get_field('currency').choices,
        # Compliance block — assembled per viewer. A field this user may not
        # see is ABSENT from the context, not present and hidden by the
        # template, so it never reaches the browser at all.
        'compliance':           build_compliance_block(employee, request.user),
        'visa_type_choices':    Employee.VISA_TYPE_CHOICES,
        'contract_type_choices': Employee.CONTRACT_TYPE_CHOICES,
        'commission_plans':     _commission_plans(),
    }
    return render(request, 'attendance/employee_profile.html', context)


# ── Section POST handlers ──────────────────────────────────────────────────────

def _handle_section_post(request, employee):
    section = request.GET.get('section', '')
    handlers = {
        'personal':     _save_personal,
        'contact':      _save_contact,
        'identity':     _save_identity,
        'employment':   _save_employment,
        'salary':       _save_salary,
        'bank':         _save_bank,
        'cost':         _save_cost,
        'document':     _save_document,
        'recoverable':  _save_recoverable,
        'onboarding':   _save_onboarding,
        'compliance':   _save_compliance,
    }
    handler = handlers.get(section)
    if not handler:
        return JsonResponse({'ok': False, 'error': f'Unknown section: {section}'}, status=400)

    try:
        handler(request, employee)
        employee.save()
        return JsonResponse({
            'ok': True,
            'completeness': employee.profile_completeness,
        })
    except Exception as exc:
        logger.exception('Employee profile save error (section=%s, id=%s)', section, employee.pk)
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)


def _get(request, key, default=''):
    return request.POST.get(key, default).strip()


def _save_personal(request, emp):
    emp.date_of_birth = _get(request, 'date_of_birth') or None
    emp.gender        = _get(request, 'gender') or None
    emp.blood_group   = _get(request, 'blood_group') or None
    if 'profile_photo' in request.FILES:
        emp.profile_photo = request.FILES['profile_photo']


def _save_contact(request, emp):
    emp.email                   = _get(request, 'email') or None
    emp.phone                   = _get(request, 'phone') or None
    emp.emergency_contact_name  = _get(request, 'emergency_contact_name') or None
    emp.emergency_contact_phone = _get(request, 'emergency_contact_phone') or None


def _save_identity(request, emp):
    emp.national_id      = _get(request, 'national_id') or None
    emp.passport_number  = _get(request, 'passport_number') or None


# ── Fields tracked for Employment History ────────────────────────────────────

# Each entry: (model_attr, change_type_key, human_label_fn)
# human_label_fn converts the raw DB value → a human-readable string for history display.

def _label_mgr(emp, mgr_id):
    """Resolve a reporting_manager_id to a display name."""
    if not mgr_id:
        return None
    try:
        mgr = Employee.objects.get(pk=int(mgr_id))
        return f"{mgr.name} ({mgr.person_id})"
    except (Employee.DoesNotExist, ValueError):
        return str(mgr_id)


_EMPLOYMENT_TRACKED = [
    # (field_attr, change_type)
    ('designation',       'designation'),
    ('department',        'department'),
    ('team',              'team'),
    ('location',          'location'),
    ('employment_status', 'employment_status'),
]
# reporting_manager_id needs special handling (FK → display name)


def _save_employment(request, emp):
    """Save employment section and auto-log any changed tracked fields."""
    # ── Snapshot before ────────────────────────────────────────────────────────
    before = {
        'designation':       emp.designation,
        'department':        emp.department,
        'team':              emp.team,
        'location':          emp.location,
        'employment_status': emp.employment_status,
        'reporting_manager': _label_mgr(emp, emp.reporting_manager_id),
    }

    # ── Apply new values ───────────────────────────────────────────────────────
    emp.employment_status = _get(request, 'employment_status') or 'active'
    emp.joining_date      = _get(request, 'joining_date') or None
    emp.notice_date       = _get(request, 'notice_date') or None
    emp.relieving_date    = _get(request, 'relieving_date') or None
    emp.designation       = _get(request, 'designation') or None
    emp.department        = _get(request, 'department') or None
    emp.location          = _get(request, 'location') or None
    emp.team              = _get(request, 'team') or None
    mgr_id = _get(request, 'reporting_manager')
    emp.reporting_manager_id = int(mgr_id) if mgr_id.isdigit() else None

    # ── Snapshot after ─────────────────────────────────────────────────────────
    after = {
        'designation':       emp.designation,
        'department':        emp.department,
        'team':              emp.team,
        'location':          emp.location,
        'employment_status': emp.employment_status,
        'reporting_manager': _label_mgr(emp, emp.reporting_manager_id),
    }

    # ── Write history rows for changed fields ──────────────────────────────────
    reason       = _get(request, 'history_reason')
    changed_by   = request.user.username if request.user.is_authenticated else 'system'
    today        = date.today()

    for field, change_type in _EMPLOYMENT_TRACKED:
        prev_val = before[field]
        new_val  = after[field]
        if prev_val != new_val:
            EmploymentHistory.objects.create(
                employee=emp,
                change_type=change_type,
                effective_date=today,
                previous_value=prev_val,
                new_value=new_val,
                reason=reason,
                changed_by=changed_by,
            )

    # reporting_manager handled separately (uses resolved display labels)
    if before['reporting_manager'] != after['reporting_manager']:
        EmploymentHistory.objects.create(
            employee=emp,
            change_type='reporting_manager',
            effective_date=today,
            previous_value=before['reporting_manager'],
            new_value=after['reporting_manager'],
            reason=reason,
            changed_by=changed_by,
        )


def _save_salary(request, emp):
    """
    Phase 5 — Save a new salary structure revision.

    Steps:
      1. Read and validate the five component values.
      2. Supersede any existing 'approved' rows for this employee.
      3. Create the new 'approved' SalaryStructure row.
      4. Sync Employee.salary to the new gross so payroll code is unaffected.
      5. Log a 'salary' EmploymentHistory row.
    """
    def _dec(key):
        raw = _get(request, key) or '0'
        try:
            from decimal import Decimal, InvalidOperation
            val = Decimal(raw)
            return val if val >= 0 else Decimal('0')
        except Exception:
            return 0

    basic           = _dec('basic')
    housing         = _dec('housing')
    transport       = _dec('transport')
    phone           = _dec('phone')
    other_allowance = _dec('other_allowance')
    currency        = _get(request, 'currency') or emp.currency or 'AED'
    revision_reason = _get(request, 'revision_reason')
    raw_date        = _get(request, 'effective_from')
    effective_from  = raw_date or str(date.today())

    from decimal import Decimal
    new_gross = sum([basic, housing, transport, phone, other_allowance], Decimal('0'))

    # Snapshot previous salary for history log
    prev_salary = emp.salary
    prev_currency = emp.currency or 'AED'

    # Supersede current approved rows
    emp.salary_structures.filter(status='approved').update(status='superseded')

    # Create new approved revision
    created_by = request.user.username if request.user.is_authenticated else 'system'
    new_structure = SalaryStructure.objects.create(
        employee=emp,
        effective_from=effective_from,
        basic=basic,
        housing=housing,
        transport=transport,
        phone=phone,
        other_allowance=other_allowance,
        currency=currency,
        revision_reason=revision_reason,
        status='approved',
        created_by=created_by,
    )

    # Keep Employee.salary in sync with new gross (payroll reads this)
    emp.salary   = new_gross
    emp.currency = currency

    # Phase 13 — audit trail
    try:
        from ..audit import log_audit, diff_fields
        from ..models import AuditLog
        log_audit(
            actor=created_by, action=AuditLog.ACTION_CREATE, instance=new_structure,
            changes=diff_fields(
                {'salary': f'{prev_currency} {prev_salary}' if prev_salary is not None else None},
                {'salary': f'{currency} {new_gross}'},
            ),
            note=f'Salary revision for {emp.name} ({emp.person_id})',
        )
    except Exception:
        pass

    # Log to EmploymentHistory
    salary_before_str = f"{prev_currency} {prev_salary}" if prev_salary is not None else None
    salary_after_str  = f"{currency} {new_gross}"
    EmploymentHistory.objects.create(
        employee=emp,
        change_type='salary',
        effective_date=date.today(),
        previous_value=salary_before_str,
        new_value=salary_after_str,
        reason=revision_reason,
        changed_by=created_by,
    )


def _save_bank(request, emp):
    """Save bank / payroll-admin section (bank details + pay cycle only).

    Salary and currency are now managed exclusively through the Salary Structure
    section (_save_salary). This handler no longer touches emp.salary or
    emp.currency.
    """
    emp.bank_name              = _get(request, 'bank_name') or None
    emp.bank_account_number    = _get(request, 'bank_account_number') or None
    emp.bank_routing_code      = _get(request, 'bank_routing_code') or None
    emp.salary_cycle_start_day = int(_get(request, 'salary_cycle_start_day') or 21)


def _save_cost(request, emp):
    """
    Phase 6 — Save a new EmployerCostSetup revision for an in-house employee.

    Each save creates a new row; no existing rows are altered.
    The most recent row (by effective_from) is treated as "current".
    """
    from decimal import Decimal

    def _dec(key):
        raw = request.POST.get(key, '0').strip() or '0'
        try:
            val = Decimal(raw)
            return val if val >= 0 else Decimal('0')
        except Exception:
            return Decimal('0')

    effective_from = _get(request, 'effective_from') or str(date.today())
    created_by     = request.user.username if request.user.is_authenticated else 'system'

    new_cost = EmployerCostSetup.objects.create(
        employee=emp,
        effective_from=effective_from,
        manpower_monthly_fee=_dec('manpower_monthly_fee'),
        visa_amortisation_monthly=_dec('visa_amortisation_monthly'),
        visa_status_change_amortisation=_dec('visa_status_change_amortisation'),
        medical_insurance_monthly=_dec('medical_insurance_monthly'),
        eos_provision_monthly=_dec('eos_provision_monthly'),
        leave_salary_provision_monthly=_dec('leave_salary_provision_monthly'),
        air_ticket_provision_monthly=_dec('air_ticket_provision_monthly'),
        recruitment_cost_allocation=_dec('recruitment_cost_allocation'),
        other_cost_monthly=_dec('other_cost_monthly'),
        notes=_get(request, 'notes'),
        created_by=created_by,
    )
    # _save_cost does not call emp.save() on extra fields — EmployerCostSetup
    # is a standalone record, not a field on Employee itself.

    # Phase 13 — audit trail
    try:
        from ..audit import log_audit
        from ..models import AuditLog
        log_audit(
            actor=created_by, action=AuditLog.ACTION_CREATE, instance=new_cost,
            note=f'Employer cost setup for {emp.name} ({emp.person_id}), total AED {new_cost.total_monthly_cost:,.2f}',
        )
    except Exception:
        pass


def _save_document(request, emp):
    """
    Phase 7 — Add a new EmployeeDocument row for an in-house employee.

    Each call creates one new document record.
    Existing documents are never modified through this handler —
    corrections go through the Django admin.
    File upload is handled via request.FILES['file'] if provided.
    """
    created_by = request.user.username if request.user.is_authenticated else 'system'

    doc = EmployeeDocument(
        employee=emp,
        document_type=_get(request, 'document_type') or 'other',
        document_number=_get(request, 'document_number'),
        issuing_country=_get(request, 'issuing_country'),
        notes=_get(request, 'notes'),
        created_by=created_by,
    )

    raw_issue  = _get(request, 'issue_date')
    raw_expiry = _get(request, 'expiry_date')
    doc.issue_date  = raw_issue  or None
    doc.expiry_date = raw_expiry or None

    if 'file' in request.FILES:
        doc.file = request.FILES['file']

    doc.save()
    # _save_document creates a standalone row — does NOT set fields on emp,
    # so the caller's emp.save() is a no-op for this section (harmless).


def _save_recoverable(request, emp):
    """
    Phase 8 — Create a new Recoverable sub-ledger row for an in-house employee.

    Each call inserts one row. Existing recoverables are never modified here —
    updates (marking settled, changing monthly_recovery, recording waivers) go
    through the Django admin or a dedicated admin action.
    """
    created_by = request.user.username if request.user.is_authenticated else 'system'

    def _dec(key, default='0'):
        raw = _get(request, key) or default
        try:
            from decimal import Decimal, InvalidOperation
            return Decimal(raw)
        except (InvalidOperation, ValueError):
            return Decimal(default)

    def _int(key, default=0):
        raw = _get(request, key)
        try:
            return int(raw)
        except (ValueError, TypeError):
            return default

    rec = Recoverable(
        employee=emp,
        recoverable_type=_get(request, 'recoverable_type') or 'other',
        description=_get(request, 'description'),
        total_amount=_dec('total_amount'),
        currency=_get(request, 'currency') or 'AED',
        monthly_recovery=_dec('monthly_recovery'),
        recovery_start_year=_int('recovery_start_year'),
        recovery_start_month=_int('recovery_start_month', default=1),
        notes=_get(request, 'notes'),
        created_by=created_by,
    )
    rec.save()
    # _save_recoverable creates a standalone row — does NOT set fields on emp,
    # so the caller's emp.save() is a no-op for this section (harmless).

    # Phase 13 — audit trail
    try:
        from ..audit import log_audit
        from ..models import AuditLog
        log_audit(
            actor=created_by, action=AuditLog.ACTION_CREATE, instance=rec,
            note=f'Recoverable for {emp.name} ({emp.person_id}): {rec.currency} {rec.total_amount:,.2f}',
        )
    except Exception:
        pass


def _save_onboarding(request, emp):
    checklist = {}
    for key in ONBOARDING_ITEMS:
        checklist[key] = request.POST.get(f'checklist_{key}') == 'on'
    emp.onboarding_checklist = checklist


# ── Compliance block ──────────────────────────────────────────────────────────

def _commission_plans():
    """Active plans for the picklist. Imported lazily — attendance must not
    depend on payroll at module import time; payroll already imports
    attendance, and a module-level pair would be a cycle waiting for a
    reordering."""
    from payroll.models import CommissionPlan
    return CommissionPlan.objects.filter(is_active=True)


def _parse_date(value):
    """'' -> None, a real date -> date. Anything else is an error, not a None.

    Silently swallowing an unparseable date would clear a probation end date
    the user believed they had just set.
    """
    if not value:
        return None
    try:
        return datetime.datetime.strptime(value, '%Y-%m-%d').date()
    except ValueError:
        raise ValueError(f'"{value}" is not a valid date (expected YYYY-MM-DD).')


def _save_compliance(request, emp):
    """Write only the fields this user is entitled to write.

    A field the caller may not write is IGNORED, not rejected — a form that
    posts every field it rendered should not fail because one of them was
    read-only for this viewer. What matters is that it does not land.
    """
    role = access.role_of(request.user)
    writable = svc_compliance.writable_fields(role, emp)
    changed = []
    for field in svc_compliance.WRITE_RULES:
        if field not in request.POST:
            continue
        if field not in writable:
            logger.warning(
                'Compliance write REFUSED: user=%s role=%s field=%s employee=%s',
                request.user.username, role or 'none', field, emp.pk)
            continue
        value = _get(request, field)
        if field == 'probation_end_date':
            setattr(emp, field, _parse_date(value))
        else:
            setattr(emp, field, value)
        changed.append(field)

    if 'confirm_review' in request.POST:
        emp.compliance_reviewed_at = timezone.now()
        emp.compliance_reviewed_by = request.user.username
        changed.append('compliance_review')

    # Deliberately NOT full_clean(). This form touches at most seven fields on
    # a row that may have been created years ago; running whole-model
    # validation would let unrelated legacy data block a compliance edit. The
    # rules that matter here are checked directly.
    if emp.probation_end_date and emp.joining_date and \
            emp.probation_end_date < emp.joining_date:
        raise ValueError('Probation cannot end before the joining date.')
    if emp.contract_type and emp.contract_type not in dict(Employee.CONTRACT_TYPE_CHOICES):
        raise ValueError('Unrecognised contract type.')
    if emp.visa_type and emp.visa_type not in dict(Employee.VISA_TYPE_CHOICES):
        raise ValueError('Unrecognised visa type.')
    from payroll.models import CommissionPlan
    if emp.commission_plan_code and not CommissionPlan.objects.filter(
            code=emp.commission_plan_code).exists():
        raise ValueError(
            f'No commission plan with code "{emp.commission_plan_code}". '
            'Create it first rather than storing a code nothing resolves to.')

    if changed:
        log_audit(request.user.username, AuditLog.ACTION_UPDATE, emp,
                  note=f'Compliance updated: {", ".join(changed)}'[:255])


@login_required
@user_passes_test(section_required('employees'), login_url='/report/')
@require_http_methods(["POST"])
def compliance_reveal(request, person_id):
    """Hand back one full masked value, and record that it happened.

    Reading an Emirates ID is an event. If nobody can answer "who looked at
    this and when", masking it on the page was theatre.
    """
    employee = get_object_or_404(Employee, person_id=person_id)
    group = (request.POST.get('group') or '').strip()
    key = (request.POST.get('key') or '').strip()

    value = svc_compliance.reveal(employee, request.user, group, key)
    if value is None:
        logger.warning(
            'Compliance REVEAL refused: user=%s role=%s group=%s key=%s employee=%s',
            request.user.username, access.role_of(request.user) or 'none',
            group, key, employee.pk)
        return JsonResponse({'ok': False, 'error': 'Not permitted, or nothing recorded.'},
                            status=403)

    log_audit(request.user.username, AuditLog.ACTION_VIEW, employee,
              note=f'Revealed {group}/{key}'[:255])
    logger.info('Compliance reveal: user=%s group=%s key=%s employee=%s',
                request.user.username, group, key, employee.pk)
    return JsonResponse({'ok': True, 'value': value})
