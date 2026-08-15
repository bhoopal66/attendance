"""
Paid Holidays — the monthly declaration screen.

Money logic lives in `payroll/services_paid_holidays.py`. This module validates
input, previews, and commits on an explicit confirmation. Nothing is written
until the operator has seen the full per-employee table.
"""

import json
import logging

from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from attendance.views.utils import MONTH_NAMES, get_selected_month_year, section_required

from . import services_paid_holidays as svc
from .models import DeductionType, PaidHolidayDeclaration

logger = logging.getLogger('payroll')


def _serialise_awards(awards):
    return [{
        'name': a['name'], 'tcr': a['tcr'], 'type': a['employee_type'],
        'currency': a['currency'], 'days': a['days'],
        'gross_used': float(a['gross_used']), 'period_days': a['period_days'],
        'daily_rate': float(a['daily_rate']), 'amount': float(a['amount']),
        'skipped': a['skipped'], 'skip_reason': a['skip_reason'],
    } for a in awards]


def _decl_payload(decl):
    if decl is None:
        return None
    return {
        'id': decl.id, 'year': decl.year, 'month': decl.month,
        'dates': decl.dates or [], 'status': decl.status,
        'status_label': decl.get_status_display(),
        'paid_day_count': decl.paid_day_count,
        'sundays': [d.isoformat() for d in decl.sunday_dates],
        'confirmed_by': decl.confirmed_by,
        'confirmed_at': decl.confirmed_at.strftime('%d %b %Y %H:%M') if decl.confirmed_at else '',
        'note': decl.note,
        'withdrawn_reason': decl.withdrawn_reason,
        'awards': [{
            'name': a.person.name if a.person else '?',
            'type': a.employee_type, 'currency': a.currency, 'days': a.days,
            'daily_rate': float(a.daily_rate), 'amount': float(a.amount),
            'skipped': a.skipped, 'skip_reason': a.skip_reason,
            'posted': a.deduction_entry_id is not None,
        } for a in decl.awards.select_related('employee', 'remote_employee')],
    }


@login_required
@user_passes_test(section_required('payroll'), login_url='/report/')
def paid_holidays(request):
    month, year = get_selected_month_year(request)
    decl = svc.declaration_for(year, month)
    dtype = DeductionType.objects.filter(
        code=PaidHolidayDeclaration.DEDUCTION_CODE).first()
    return render(request, 'payroll/paid_holidays.html', {
        'year': year, 'month': month,
        'month_name': MONTH_NAMES[month] if month < len(MONTH_NAMES) else str(month),
        'declaration': _decl_payload(decl),
        'suggested': svc.suggested_dates(year, month),
        'type_ok': bool(dtype and dtype.is_active),
        'type_name': dtype.name if dtype else PaidHolidayDeclaration.DEDUCTION_CODE,
        'recent': [{
            'year': d.year, 'month': d.month,
            'month_name': MONTH_NAMES[d.month] if d.month < len(MONTH_NAMES) else str(d.month),
            'status': d.status, 'days': d.paid_day_count,
            'confirmed_by': d.confirmed_by,
        } for d in PaidHolidayDeclaration.objects.all()[:12]],
    })


@login_required
@user_passes_test(section_required('payroll'), login_url='/report/')
@require_http_methods(["POST"])
def preview(request):
    """What every employee would receive. Writes nothing."""
    try:
        data = json.loads(request.body or '{}')
        year, month = int(data.get('year')), int(data.get('month'))
    except (json.JSONDecodeError, TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Invalid request'}, status=400)
    dates = data.get('dates') or []
    if not isinstance(dates, list):
        return JsonResponse({'success': False, 'error': 'Dates must be a list.'}, status=400)

    try:
        awards = svc.build_awards(year, month, dates)
    except Exception as exc:
        logger.exception('Paid holiday preview failed')
        return JsonResponse({'success': False, 'error': f'Could not build the preview: {exc}'},
                            status=500)

    totals = {k: float(v) for k, v in svc.totals_by_currency(awards).items()}
    payable = [d for d in dates if d not in
               {s for s in dates if _is_sunday(s)}]
    return JsonResponse({
        'success': True,
        'awards': _serialise_awards(awards),
        'totals': totals,
        'payable_days': len(payable),
        'sundays': [d for d in dates if _is_sunday(d)],
        'eligible': sum(1 for a in awards if not a['skipped']),
        'skipped': sum(1 for a in awards if a['skipped']),
    })


def _is_sunday(iso):
    import datetime
    try:
        return datetime.date.fromisoformat(iso).weekday() == 6
    except (TypeError, ValueError):
        return False


@login_required
@user_passes_test(section_required('payroll'), login_url='/report/')
@require_http_methods(["POST"])
def confirm(request):
    """Record the declaration and write the additions."""
    try:
        data = json.loads(request.body or '{}')
        year, month = int(data.get('year')), int(data.get('month'))
    except (json.JSONDecodeError, TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Invalid request'}, status=400)

    try:
        decl, created = svc.confirm(year, month, data.get('dates') or [],
                                    actor=request.user.username,
                                    note=(data.get('note') or '').strip())
    except (ValueError, ValidationError) as exc:
        msg = '; '.join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
        return JsonResponse({'success': False, 'error': msg}, status=400)
    except Exception as exc:
        logger.exception('Paid holiday confirm failed')
        return JsonResponse({'success': False, 'error': f'Could not confirm: {exc}'}, status=500)

    return JsonResponse({'success': True, 'created': created,
                         'declaration': _decl_payload(decl)})


@login_required
@user_passes_test(section_required('payroll'), login_url='/report/')
@require_http_methods(["POST"])
def withdraw(request):
    """Undo a confirmation, leaving anything already paid in place."""
    try:
        data = json.loads(request.body or '{}')
        decl = PaidHolidayDeclaration.objects.get(id=int(data.get('id')))
    except (json.JSONDecodeError, PaidHolidayDeclaration.DoesNotExist, TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Declaration not found'}, status=404)
    if decl.status != PaidHolidayDeclaration.STATUS_CONFIRMED:
        return JsonResponse({'success': False,
                             'error': f'This declaration is {decl.get_status_display().lower()}.'},
                            status=400)
    removed, kept = svc.withdraw(decl, actor=request.user.username,
                                 reason=(data.get('reason') or '').strip())
    return JsonResponse({'success': True, 'removed': removed, 'kept': kept,
                         'declaration': _decl_payload(decl)})
