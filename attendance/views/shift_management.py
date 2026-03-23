"""
Views for managing special shift periods (e.g., Ramadan reduced hours).
"""

import json
import logging
import datetime

from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from ..models import SpecialShiftPeriod
from .utils import superuser_required

logger = logging.getLogger('attendance')


@login_required
@user_passes_test(superuser_required, login_url='/report/')
def special_shift_periods(request):
    """Display and manage special shift periods (Ramadan, etc.)."""
    today = datetime.date.today()
    periods = SpecialShiftPeriod.objects.all()

    for p in periods:
        if p.end_date < today:
            p.period_status = 'past'
        elif p.start_date > today:
            p.period_status = 'upcoming'
        else:
            p.period_status = 'active'

    return render(request, 'attendance/special_shift_periods.html', {
        'periods': periods,
        'today': today,
    })


@login_required
@user_passes_test(superuser_required, login_url='/report/')
@require_http_methods(["POST"])
def add_special_shift_period(request):
    """Create a new special shift period."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    try:
        period = _build_period_from_data(data)
        period.full_clean()
        period.save()
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)

    logger.info("Special shift period added: %s by %s", period.name, request.user.username)
    return JsonResponse({'success': True, 'period': _serialize_period(period)})


@login_required
@user_passes_test(superuser_required, login_url='/report/')
@require_http_methods(["POST"])
def update_special_shift_period(request, period_id):
    """Update an existing special shift period."""
    try:
        period = SpecialShiftPeriod.objects.get(id=period_id)
    except SpecialShiftPeriod.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Period not found'}, status=404)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    try:
        _apply_data_to_period(period, data)
        period.full_clean()
        period.save()
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)

    logger.info("Special shift period updated: %s (id=%s) by %s", period.name, period_id, request.user.username)
    return JsonResponse({'success': True, 'period': _serialize_period(period)})


@login_required
@user_passes_test(superuser_required, login_url='/report/')
@require_http_methods(["POST"])
def delete_special_shift_period(request, period_id):
    """Delete a special shift period."""
    try:
        period = SpecialShiftPeriod.objects.get(id=period_id)
    except SpecialShiftPeriod.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Period not found'}, status=404)

    name = period.name
    period.delete()
    logger.info("Special shift period deleted: %s (id=%s) by %s", name, period_id, request.user.username)
    return JsonResponse({'success': True})


# ── helpers ──────────────────────────────────────────────────────────────────

def _parse_time(value):
    """Parse 'HH:MM' string to datetime.time. Returns None if blank."""
    if not value:
        return None
    parts = value.strip().split(':')
    if len(parts) != 2:
        raise ValueError(f"Invalid time format: {value!r}. Expected HH:MM.")
    return datetime.time(int(parts[0]), int(parts[1]))


def _build_period_from_data(data):
    period = SpecialShiftPeriod()
    _apply_data_to_period(period, data)
    return period


def _apply_data_to_period(period, data):
    period.name = (data.get('name') or '').strip()
    if not period.name:
        raise ValueError("Name is required.")
    period.start_date = datetime.date.fromisoformat(data['start_date'])
    period.end_date = datetime.date.fromisoformat(data['end_date'])
    period.shift_start = _parse_time(data['shift_start'])
    period.shift_end = _parse_time(data['shift_end'])
    period.sat_shift_start = _parse_time(data.get('sat_shift_start'))
    period.sat_shift_end = _parse_time(data.get('sat_shift_end'))
    period.notes = (data.get('notes') or '').strip()


def _serialize_period(period):
    today = datetime.date.today()
    if period.end_date < today:
        status = 'past'
    elif period.start_date > today:
        status = 'upcoming'
    else:
        status = 'active'
    return {
        'id': period.id,
        'name': period.name,
        'start_date': period.start_date.isoformat(),
        'end_date': period.end_date.isoformat(),
        'shift_start': period.shift_start.strftime('%H:%M'),
        'shift_end': period.shift_end.strftime('%H:%M'),
        'sat_shift_start': period.sat_shift_start.strftime('%H:%M') if period.sat_shift_start else '',
        'sat_shift_end': period.sat_shift_end.strftime('%H:%M') if period.sat_shift_end else '',
        'notes': period.notes,
        'status': status,
    }
