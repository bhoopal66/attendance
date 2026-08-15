"""
Phase 2 - Deduction Master administration.

The page where a user configures the deduction and addition types that the
rest of payroll offers. Kept in its own module rather than added to
`payroll/views.py`, which is already ~275 KB and is the thing Phase 1 exists
to stop growing.

Access: the 'payroll' section grant, the same gate as the payroll dashboard.
Every mutation is written to AuditLog with a field-level diff, because this
table decides how money is labelled and grouped.
"""

import json
import logging

from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from attendance.audit import diff_fields, log_audit
from attendance.models import AuditLog
from attendance.views.utils import section_required

from .models import DeductionEntry, DeductionType

logger = logging.getLogger('payroll')


#: Fields a user may set from the page. `is_system` and `code`-on-existing are
#: deliberately absent: a request cannot promote its own type to built-in, and
#: cannot rename a code that entries already point at.
EDITABLE_FIELDS = (
    'name', 'entry_type', 'classification', 'description',
    'allow_manual_entry', 'allow_split_months', 'rolls_up_to_other',
    'requires_note', 'gl_account_code', 'colour', 'sort_order',
)

_BOOL_FIELDS = {'allow_manual_entry', 'allow_split_months',
                'rolls_up_to_other', 'requires_note'}


def _snapshot(obj):
    """Field values for the audit diff."""
    data = {f: getattr(obj, f) for f in EDITABLE_FIELDS}
    data['code'] = obj.code
    data['is_active'] = obj.is_active
    return data


#: Deduction codes that have their own column on the payroll dashboard.
_COLUMN_NAMES = {
    'late_deduction': 'Late',
    'leave_deduction': 'Leave',
    'advance': 'Advance',
}


def _column_label(t):
    """Which dashboard column this type's amount appears in.

    Shown on the page because it is the one setting here that can make the
    itemized deduction columns stop summing to the Deductions total, and a
    user has no other way to see where an amount will land.
    """
    if t.entry_type != 'deduction':
        return 'Additions'
    if t.rolls_up_to_other:
        return 'Other'
    return _COLUMN_NAMES.get(t.code, 'Other')


def _serialise(t, usage):
    return {
        'id': t.id,
        'code': t.code,
        'name': t.name,
        'entry_type': t.entry_type,
        'classification': t.classification,
        'description': t.description,
        'is_active': t.is_active,
        'is_system': t.is_system,
        'allow_manual_entry': t.allow_manual_entry,
        'allow_split_months': t.allow_split_months,
        'rolls_up_to_other': t.rolls_up_to_other,
        'requires_note': t.requires_note,
        'gl_account_code': t.gl_account_code,
        'colour': t.colour,
        'badge_colour': t.badge_colour,
        'sort_order': t.sort_order,
        'column_label': _column_label(t),
        'entry_count': usage.get(t.code, 0),
    }


def _type_from_request(request):
    """Resolve the DeductionType named by an `id` in the JSON body.

    The id travels in the body rather than the URL so the templates can use
    {% url %} throughout instead of assembling paths in JavaScript - a
    hardcoded '/payroll/...' string silently breaks the moment the app is
    mounted anywhere else.
    """
    try:
        data = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return None
    try:
        return DeductionType.objects.get(id=int(data.get('id')))
    except (DeductionType.DoesNotExist, TypeError, ValueError):
        return None


def _usage_counts():
    """{code: number of DeductionEntry rows} in one query.

    Shown next to each type so nobody deactivates or deletes a type without
    seeing how much history points at it.
    """
    from django.db.models import Count
    return {
        row['category']: row['n']
        for row in DeductionEntry.objects.values('category').annotate(n=Count('id'))
    }


@login_required
@user_passes_test(section_required('payroll'), login_url='/report/')
def deduction_types(request):
    """Deduction & Addition Types - list and configure."""
    usage = _usage_counts()
    types = list(DeductionType.objects.all())
    rows = [_serialise(t, usage) for t in types]

    # A code with entries but no master row can only appear if somebody wrote
    # a category value directly into the database, or removed a type that had
    # history. Surfacing it beats letting it render as a blank label.
    known = {t.code for t in types}
    orphans = [
        {'code': code, 'entry_count': n}
        for code, n in sorted(usage.items()) if code not in known
    ]

    return render(request, 'payroll/deduction_types.html', {
        'types': rows,
        'orphans': orphans,
        'deduction_count': sum(1 for r in rows if r['entry_type'] == 'deduction'),
        'addition_count': sum(1 for r in rows if r['entry_type'] == 'addition'),
        'inactive_count': sum(1 for r in rows if not r['is_active']),
        'entry_type_choices': DeductionType.ENTRY_TYPES,
        'classification_choices': DeductionType.CLASSIFICATIONS,
        'dedicated_column_codes': list(DeductionType.DEDICATED_COLUMN_CODES),
    })


@login_required
@user_passes_test(section_required('payroll'), login_url='/report/')
@require_http_methods(["POST"])
def deduction_type_save(request):
    """Create a type (no `id`) or update one (with `id`)."""
    try:
        data = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    type_id = data.get('id')
    creating = not type_id

    if creating:
        obj = DeductionType(
            code=(data.get('code') or '').strip().lower(),
            created_by=request.user.username,
        )
        before = {}
    else:
        try:
            obj = DeductionType.objects.get(id=type_id)
        except DeductionType.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Type not found'}, status=404)
        before = _snapshot(obj)

    for field in EDITABLE_FIELDS:
        if field not in data:
            continue
        value = data[field]
        if field in _BOOL_FIELDS:
            value = bool(value)
        elif field == 'sort_order':
            try:
                value = max(0, int(value))
            except (TypeError, ValueError):
                return JsonResponse(
                    {'success': False, 'error': 'Sort order must be a whole number.'},
                    status=400)
        elif field == 'entry_type' and not creating:
            # Guarded again in clean(); rejected here so the message is clear.
            if value != obj.entry_type and obj.has_entries():
                return JsonResponse({
                    'success': False,
                    'error': f'{obj.name} already has {obj.entry_count()} entry(s). '
                             'Switching between deduction and addition would reverse '
                             'the sign of money already recorded.',
                }, status=400)
        elif isinstance(value, str):
            value = value.strip()
        setattr(obj, field, value)

    obj.updated_by = request.user.username

    try:
        obj.full_clean(exclude=['created_at', 'updated_at'])
    except ValidationError as exc:
        first = next(iter(exc.message_dict.values()))[0]
        return JsonResponse({'success': False, 'error': first}, status=400)

    try:
        obj.save()
    except IntegrityError:
        return JsonResponse(
            {'success': False, 'error': f'The code "{obj.code}" is already in use.'},
            status=400)

    log_audit(
        request.user.username,
        AuditLog.ACTION_CREATE if creating else AuditLog.ACTION_UPDATE,
        obj,
        changes=None if creating else diff_fields(before, _snapshot(obj)),
        note='Deduction type created' if creating else 'Deduction type updated',
    )
    logger.info('DeductionType %s by %s: %s',
                'created' if creating else 'updated', request.user.username, obj.code)

    usage = _usage_counts()
    return JsonResponse({'success': True, 'type': _serialise(obj, usage)})


@login_required
@user_passes_test(section_required('payroll'), login_url='/report/')
@require_http_methods(["POST"])
def deduction_type_toggle(request):
    """Activate or deactivate a type.

    Deactivating hides it from the entry form. It does NOT stop deductions
    already scheduled against it - those are money the employee owes, and
    cancelling them is a separate, deliberate act on each entry.
    """
    obj = _type_from_request(request)
    if obj is None:
        return JsonResponse({'success': False, 'error': 'Type not found'}, status=404)

    was = obj.is_active
    obj.is_active = not was
    obj.updated_by = request.user.username
    obj.save(update_fields=['is_active', 'updated_by', 'updated_at'])
    # save(update_fields=...) still runs our save() override, which clears the
    # cache - but be explicit, because that is easy to break later.
    from .models import invalidate_deduction_type_cache
    invalidate_deduction_type_cache()

    log_audit(request.user.username, AuditLog.ACTION_UPDATE, obj,
              changes={'is_active': [str(was), str(obj.is_active)]},
              note='Deduction type ' + ('activated' if obj.is_active else 'deactivated'))
    logger.info('DeductionType %s %s by %s',
                obj.code, 'activated' if obj.is_active else 'deactivated',
                request.user.username)

    return JsonResponse({'success': True, 'is_active': obj.is_active})


@login_required
@user_passes_test(section_required('payroll'), login_url='/report/')
@require_http_methods(["POST"])
def deduction_type_delete(request):
    """Delete a custom type. Refused for built-ins and for anything with history."""
    obj = _type_from_request(request)
    if obj is None:
        return JsonResponse({'success': False, 'error': 'Type not found'}, status=404)

    type_id, label, code = obj.id, str(obj), obj.code
    try:
        obj.delete()
    except ValidationError as exc:
        return JsonResponse({'success': False, 'error': '; '.join(exc.messages)}, status=400)

    obj.pk = type_id  # log_audit needs a pk to record against
    log_audit(request.user.username, AuditLog.ACTION_DELETE, obj,
              note=f'Deduction type deleted: {label}')
    logger.info('DeductionType deleted: %s by %s', code, request.user.username)
    return JsonResponse({'success': True})
