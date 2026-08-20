"""Company assets issued to an employee — custody and condition, not money.

Marking an asset lost creates a linked Recoverable so the cost is tracked in
exactly one place rather than manually duplicated between an asset note and
a separate recovery entry.
"""
import logging

from django.core.exceptions import ValidationError
from django.db import transaction

logger = logging.getLogger('attendance')


def _person_filter(person):
    from attendance.models import Employee
    if isinstance(person, Employee):
        return {'employee': person, 'remote_employee': None}
    return {'employee': None, 'remote_employee': person}


def issue_asset(person, actor='', **fields):
    from attendance.models import AuditLog, EmployeeAsset
    from attendance.audit import log_audit

    asset = EmployeeAsset(**_person_filter(person), issued_by=actor or '',
                           created_by=actor or '', **fields)
    asset.full_clean(exclude=['employee', 'remote_employee', 'recoverable'])
    asset.save()

    log_audit(actor, AuditLog.ACTION_CREATE, asset,
              note=f'Asset issued to {person}: {asset.get_asset_type_display()}'[:255])
    return asset


def return_asset(asset, actor, returned_date, condition_current=''):
    from attendance.models import AuditLog
    from attendance.audit import log_audit

    before_status = asset.status
    asset.status = 'returned'
    asset.returned_date = returned_date
    if condition_current:
        asset.condition_current = condition_current
    asset.save(update_fields=['status', 'returned_date', 'condition_current', 'updated_at'])

    log_audit(actor, AuditLog.ACTION_UPDATE, asset,
              changes={'status': [before_status, 'returned']},
              note=f'Asset returned {returned_date}')
    return asset


@transaction.atomic
def mark_lost(asset, actor, recovery_amount=None, currency='AED', reason=''):
    """Mark an asset lost/damaged and, when a recovery amount is given,
    create a linked Recoverable so the cost isn't tracked in two places."""
    from attendance.models import AuditLog, Recoverable
    from attendance.audit import log_audit

    before_status = asset.status
    asset.status = 'lost'
    asset.condition_current = 'lost'
    update_fields = ['status', 'condition_current', 'updated_at']

    if recovery_amount:
        rec = Recoverable.objects.create(
            employee=asset.employee, remote_employee=asset.remote_employee,
            recoverable_type='asset',
            description=f'Lost/damaged asset: {asset.get_asset_type_display()}'
                        + (f' — {asset.asset_tag}' if asset.asset_tag else ''),
            total_amount=recovery_amount, currency=currency,
            recovery_start_year=asset.issued_date.year if asset.issued_date else 0,
            recovery_start_month=asset.issued_date.month if asset.issued_date else 1,
            notes=reason, created_by=actor or '',
        )
        asset.recoverable = rec
        update_fields.append('recoverable')
        log_audit(actor, AuditLog.ACTION_CREATE, rec,
                  note=f'Recoverable created for lost asset ({asset.get_asset_type_display()})'[:255])

    asset.save(update_fields=update_fields)
    log_audit(actor, AuditLog.ACTION_UPDATE, asset,
              changes={'status': [before_status, 'lost']},
              note=f'Asset marked lost: {reason}'[:255])
    return asset


def asset_history(person):
    from attendance.models import EmployeeAsset
    return EmployeeAsset.objects.filter(**_person_filter(person)).order_by('-issued_date', '-id')
