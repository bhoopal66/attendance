"""
Audit logging helper — Phase 13.

log_audit() is called explicitly at known mutation points where the real
actor (request.user or an already-known username string) is available.
It never raises — a failure to write an audit row must never block the
underlying business operation it's describing.

Deploy as: attendance/audit.py
"""

import logging

logger = logging.getLogger('attendance')


def log_audit(actor, action, instance, changes=None, note=''):
    """
    Write one AuditLog row.

    actor      — username string (or '' / 'system' if unknown)
    action     — one of AuditLog.ACTION_CREATE / _UPDATE / _DELETE / _TRANSITION
    instance   — the model instance being audited (must have a .pk)
    changes    — optional {field: [old, new], ...} dict
    note       — optional free-text context (e.g. 'draft -> review')

    Returns the created AuditLog row, or None if logging failed (never raises).
    """
    from .models import AuditLog

    try:
        model = instance.__class__
        return AuditLog.objects.create(
            actor=actor or 'system',
            action=action,
            app_label=model._meta.app_label,
            model_name=model._meta.model_name,
            object_id=str(instance.pk) if instance.pk is not None else '',
            object_repr=str(instance)[:255],
            changes=changes or {},
            note=note[:255] if note else '',
        )
    except Exception:
        logger.exception(
            'log_audit failed for %s #%s (action=%s) — business operation still proceeded',
            getattr(instance, '__class__', type(instance)).__name__,
            getattr(instance, 'pk', '?'),
            action,
        )
        return None


def diff_fields(before: dict, after: dict):
    """
    Build a {field: [old, new]} dict for keys present in both snapshots
    whose values differ. Values are stringified for safe JSON storage.
    """
    changes = {}
    for key in after:
        if key not in before:
            continue
        old_val, new_val = before[key], after[key]
        if old_val != new_val:
            changes[key] = [
                str(old_val) if old_val is not None else None,
                str(new_val) if new_val is not None else None,
            ]
    return changes
