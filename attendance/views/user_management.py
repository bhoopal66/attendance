"""
Custom User Management views for admin login accounts (django.contrib.auth.User).

Access is restricted to superusers flagged as IT Admin (see UserProfile.is_it_admin),
which is a narrower gate than the general superuser_required check used elsewhere.
"""

import json
import logging
from collections import OrderedDict

from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from ..models import UserProfile
from .utils import it_admin_required, NAV_SECTIONS, NAV_SECTION_KEYS

logger = logging.getLogger('attendance')


def _clean_allowed_sections(raw):
    """Filter incoming section keys down to known, valid ones."""
    if not isinstance(raw, list):
        return []
    return [key for key in raw if key in NAV_SECTION_KEYS]


def _grouped_nav_permissions():
    """NAV_SECTIONS clustered by group label, preserving definition order."""
    groups = OrderedDict()
    for key, label, group in NAV_SECTIONS:
        groups.setdefault(group, []).append((key, label))
    return groups


@login_required
@user_passes_test(it_admin_required)
def user_management(request):
    """Admin page to manage Django auth User accounts."""
    users = User.objects.select_related('profile').order_by('username')

    context = {
        'users': users,
        'total_count': users.count(),
        'superuser_count': users.filter(is_superuser=True).count(),
        'active_count': users.filter(is_active=True).count(),
        'nav_permission_groups': _grouped_nav_permissions(),
    }
    return render(request, 'attendance/user_management.html', context)


@login_required
@user_passes_test(it_admin_required)
@require_http_methods(["POST"])
def create_user(request):
    """Create a new Django auth User account."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    email = (data.get('email') or '').strip()

    if not username:
        return JsonResponse({'success': False, 'error': 'Username is required'}, status=400)
    if User.objects.filter(username__iexact=username).exists():
        return JsonResponse({'success': False, 'error': 'A user with this username already exists'}, status=400)
    if not password:
        return JsonResponse({'success': False, 'error': 'Password is required'}, status=400)

    try:
        validate_password(password)
    except ValidationError as e:
        return JsonResponse({'success': False, 'error': ' '.join(e.messages)}, status=400)

    user = User.objects.create_user(
        username=username,
        email=email,
        password=password,
        first_name=(data.get('first_name') or '').strip(),
        last_name=(data.get('last_name') or '').strip(),
        is_active=bool(data.get('is_active', True)),
        is_staff=bool(data.get('is_staff', False)),
        is_superuser=bool(data.get('is_superuser', False)),
    )
    UserProfile.objects.create(
        user=user,
        is_it_admin=bool(data.get('is_it_admin', False)),
        sections_restricted=bool(data.get('sections_restricted', False)),
        allowed_sections=_clean_allowed_sections(data.get('allowed_sections')),
    )

    logger.info(f"User account '{username}' created by {request.user.username}")
    return JsonResponse({'success': True})


@login_required
@user_passes_test(it_admin_required)
@require_http_methods(["POST"])
def update_user(request, user_id):
    """Update an existing Django auth User account."""
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'User not found'}, status=404)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    if 'email' in data:
        user.email = (data.get('email') or '').strip()
    if 'first_name' in data:
        user.first_name = (data.get('first_name') or '').strip()
    if 'last_name' in data:
        user.last_name = (data.get('last_name') or '').strip()

    # Guard against locking everyone out of Django Admin / this page.
    revoking_superuser = 'is_superuser' in data and not bool(data.get('is_superuser'))
    revoking_active = 'is_active' in data and not bool(data.get('is_active'))
    if (revoking_superuser or revoking_active) and user.is_superuser:
        if User.objects.filter(is_superuser=True, is_active=True).exclude(id=user.id).count() == 0:
            return JsonResponse({'success': False, 'error': 'Cannot remove the last active superuser'}, status=400)

    if 'is_active' in data:
        user.is_active = bool(data.get('is_active'))
    if 'is_staff' in data:
        user.is_staff = bool(data.get('is_staff'))
    if 'is_superuser' in data:
        user.is_superuser = bool(data.get('is_superuser'))

    password = data.get('password')
    if password:
        try:
            validate_password(password, user=user)
        except ValidationError as e:
            return JsonResponse({'success': False, 'error': ' '.join(e.messages)}, status=400)
        user.set_password(password)

    user.save()

    if 'is_it_admin' in data or 'sections_restricted' in data or 'allowed_sections' in data:
        profile, _ = UserProfile.objects.get_or_create(user=user)
        if 'is_it_admin' in data:
            profile.is_it_admin = bool(data.get('is_it_admin'))
        if 'sections_restricted' in data:
            profile.sections_restricted = bool(data.get('sections_restricted'))
        if 'allowed_sections' in data:
            profile.allowed_sections = _clean_allowed_sections(data.get('allowed_sections'))
        profile.save()

    logger.info(f"User account '{user.username}' updated by {request.user.username}")
    return JsonResponse({'success': True})


@login_required
@user_passes_test(it_admin_required)
@require_http_methods(["POST"])
def delete_user(request, user_id):
    """Delete a Django auth User account."""
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'User not found'}, status=404)

    if user.id == request.user.id:
        return JsonResponse({'success': False, 'error': 'You cannot delete your own account'}, status=400)

    if user.is_superuser and User.objects.filter(is_superuser=True, is_active=True).exclude(id=user.id).count() == 0:
        return JsonResponse({'success': False, 'error': 'Cannot delete the last active superuser'}, status=400)

    username = user.username
    user.delete()
    logger.info(f"User account '{username}' deleted by {request.user.username}")
    return JsonResponse({'success': True})
