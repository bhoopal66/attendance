"""
Phase 4 - deduction rules & limits: the screens.

Two things on one page: the rules themselves, and a pre-payroll check that
runs every active rule over a chosen month.

Access: the 'payroll' section grant. Activating, editing or retiring a rule is
written to AuditLog - a ceiling that caps someone's salary needs a trail.
"""

import json
import logging
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from attendance.audit import diff_fields, log_audit
from attendance.models import AuditLog
from attendance.views.utils import get_selected_month_year, section_required

from . import services_deduction_rules as rules_svc
from .models import DeductionRule, deduction_category_choices

logger = logging.getLogger('payroll')

EDITABLE = ('name', 'description', 'scope', 'deduction_code', 'basis',
            'max_percent', 'max_amount', 'amount_currency', 'applies_to',
            'department', 'enforcement', 'is_active', 'legal_reference',
            'effective_from_year', 'effective_from_month',
            'effective_to_year', 'effective_to_month')

_DECIMAL_FIELDS = {'max_percent', 'max_amount'}
_INT_FIELDS = {'effective_from_year', 'effective_from_month',
               'effective_to_year', 'effective_to_month'}


def _snapshot(rule):
    return {f: getattr(rule, f) for f in EDITABLE}


def _serialise(rule):
    return {
        'id': rule.id,
        'code': rule.code,
        'name': rule.name,
        'description': rule.description,
        'scope': rule.scope,
        'scope_label': rule.get_scope_display(),
        'deduction_code': rule.deduction_code,
        'basis': rule.basis,
        'basis_label': rule.get_basis_display(),
        'max_percent': float(rule.max_percent) if rule.max_percent is not None else None,
        'max_amount': float(rule.max_amount) if rule.max_amount is not None else None,
        'amount_currency': rule.amount_currency,
        'applies_to': rule.applies_to,
        'applies_label': rule.get_applies_to_display(),
        'department': rule.department,
        'enforcement': rule.enforcement,
        'enforcement_label': rule.get_enforcement_display(),
        'is_active': rule.is_active,
        'legal_reference': rule.legal_reference,
        'ceiling_label': rule.ceiling_label,
        'effective_from_year': rule.effective_from_year,
        'effective_from_month': rule.effective_from_month,
        'effective_to_year': rule.effective_to_year,
        'effective_to_month': rule.effective_to_month,
    }


@login_required
@user_passes_test(section_required('payroll'), login_url='/report/')
def deduction_rules(request):
    """Rules, plus a pre-payroll check for the selected month."""
    month, year = get_selected_month_year(request)
    all_rules = list(DeductionRule.objects.all())
    active = [r for r in all_rules if r.is_active and r.is_effective_in(year, month)]

    check_rows = []
    for row in rules_svc.check_month(year, month, rules=active):
        check_rows.append({
            'name': row['employee'].name,
            'tcr': getattr(row['employee'], 'tcr_id', '') or '',
            'employee_type': row['employee_type'],
            'breaches': [{
                'rule': r.rule.name,
                'enforcement': r.rule.enforcement,
                'reason': r.reason,
                'applied': float(r.applied),
                'ceiling': float(r.ceiling) if r.ceiling is not None else None,
                'excess': float(r.excess) if r.excess is not None else None,
            } for r in row['breaches']],
            'unevaluated': [{'rule': r.rule.name, 'reason': r.reason}
                            for r in row['unevaluated']],
            'passed': sum(1 for r in row['results'] if r.outcome == 'pass'),
        })

    return render(request, 'payroll/deduction_rules.html', {
        'rules': [_serialise(r) for r in all_rules],
        'check_rows': check_rows,
        'check_year': year,
        'check_month': month,
        'active_count': len(active),
        'breach_count': sum(len(r['breaches']) for r in check_rows),
        'uneval_count': sum(len(r['unevaluated']) for r in check_rows),
        'checked_employees': len(check_rows),
        'scope_choices': DeductionRule.SCOPE_CHOICES,
        'basis_choices': DeductionRule.BASIS_CHOICES,
        'applies_choices': DeductionRule.APPLIES_CHOICES,
        'enforce_choices': DeductionRule.ENFORCE_CHOICES,
        'deduction_codes': deduction_category_choices(include_inactive=True),
    })


@login_required
@user_passes_test(section_required('payroll'), login_url='/report/')
@require_http_methods(["POST"])
def rule_save(request):
    """Create or update a rule."""
    try:
        data = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    rule_id = data.get('id')
    creating = not rule_id
    if creating:
        rule = DeductionRule(code=(data.get('code') or '').strip().lower(),
                             created_by=request.user.username)
        before = {}
    else:
        try:
            rule = DeductionRule.objects.get(id=int(rule_id))
        except (DeductionRule.DoesNotExist, TypeError, ValueError):
            return JsonResponse({'success': False, 'error': 'Rule not found'}, status=404)
        before = _snapshot(rule)

    for field in EDITABLE:
        if field not in data:
            continue
        value = data[field]
        if field in _DECIMAL_FIELDS:
            if value in (None, ''):
                value = None
            else:
                try:
                    value = Decimal(str(value))
                except InvalidOperation:
                    return JsonResponse({'success': False,
                                         'error': f'{field.replace("_", " ")} must be a number.'},
                                        status=400)
        elif field in _INT_FIELDS:
            if value in (None, ''):
                value = None
            else:
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    return JsonResponse({'success': False,
                                         'error': f'{field.replace("_", " ")} must be a whole number.'},
                                        status=400)
        elif field == 'is_active':
            value = bool(value)
        elif isinstance(value, str):
            value = value.strip()
        setattr(rule, field, value)

    rule.updated_by = request.user.username
    try:
        rule.full_clean(exclude=['created_at', 'updated_at'])
    except ValidationError as exc:
        msgs = exc.message_dict
        # __all__ carries the "set a percentage or an amount" style errors.
        first = (msgs.get('__all__') or next(iter(msgs.values())))[0]
        return JsonResponse({'success': False, 'error': first}, status=400)

    try:
        rule.save()
    except IntegrityError:
        return JsonResponse({'success': False,
                             'error': f'The code "{rule.code}" is already in use.'}, status=400)

    changes = None if creating else diff_fields(before, _snapshot(rule))
    log_audit(request.user.username,
              AuditLog.ACTION_CREATE if creating else AuditLog.ACTION_UPDATE, rule,
              changes=changes,
              note=('Deduction rule created' if creating else 'Deduction rule updated')
                   + (f' — ACTIVE, {rule.ceiling_label}' if rule.is_active else ' — inactive'))
    logger.info('DeductionRule %s %s by %s (active=%s)', rule.code,
                'created' if creating else 'updated', request.user.username, rule.is_active)
    return JsonResponse({'success': True, 'rule': _serialise(rule)})


@login_required
@user_passes_test(section_required('payroll'), login_url='/report/')
@require_http_methods(["POST"])
def rule_toggle(request):
    """Switch a rule on or off.

    Turning one ON is the moment it starts affecting real entries, so the same
    `legal_reference` guard applies here as in the form — this endpoint cannot
    be used to bypass it.
    """
    try:
        data = json.loads(request.body or '{}')
        rule = DeductionRule.objects.get(id=int(data.get('id')))
    except (json.JSONDecodeError, DeductionRule.DoesNotExist, TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Rule not found'}, status=404)

    was = rule.is_active
    rule.is_active = not was
    rule.updated_by = request.user.username
    try:
        rule.full_clean(exclude=['created_at', 'updated_at'])
    except ValidationError as exc:
        msgs = exc.message_dict
        first = (msgs.get('__all__') or next(iter(msgs.values())))[0]
        return JsonResponse({'success': False, 'error': first}, status=400)
    rule.save()

    log_audit(request.user.username, AuditLog.ACTION_UPDATE, rule,
              changes={'is_active': [str(was), str(rule.is_active)]},
              note=('Rule ACTIVATED — ' + rule.ceiling_label) if rule.is_active
                   else 'Rule deactivated')
    logger.info('DeductionRule %s %s by %s', rule.code,
                'activated' if rule.is_active else 'deactivated', request.user.username)
    return JsonResponse({'success': True, 'is_active': rule.is_active})


@login_required
@user_passes_test(section_required('payroll'), login_url='/report/')
@require_http_methods(["POST"])
def rule_delete(request):
    """Delete a rule outright. Refused while it is active."""
    try:
        data = json.loads(request.body or '{}')
        rule = DeductionRule.objects.get(id=int(data.get('id')))
    except (json.JSONDecodeError, DeductionRule.DoesNotExist, TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Rule not found'}, status=404)
    if rule.is_active:
        return JsonResponse({
            'success': False,
            'error': 'Deactivate the rule first. Deleting an enforced ceiling outright '
                     'removes it with no record of what was being applied.'}, status=400)
    pk, label = rule.id, str(rule)
    rule.delete()
    rule.pk = pk
    log_audit(request.user.username, AuditLog.ACTION_DELETE, rule,
              note=f'Deduction rule deleted: {label}')
    return JsonResponse({'success': True})
