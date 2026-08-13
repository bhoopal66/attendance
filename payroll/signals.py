"""
Audit signals — Phase 13.

DeductionEntry is edited exclusively from payroll/views.py (the 238KB
monolith, never edited directly by this project) and has no dedicated
admin registered, so there is no view-layer or admin-layer call site where
a real request.user is available to log_audit() explicitly.

This signal is the safe, additive fallback for that one model: it captures
every create/update/delete regardless of where the change came from, at
the cost of the actor always being 'system' (Django signals do not carry
the current request/user). If real-actor attribution for DeductionEntry
becomes a requirement, the correct follow-up is a small thread-local
current-user middleware — a deliberate, separately-reviewed change, not
something to bundle in here silently.

Deploy as: payroll/signals.py
Wired via payroll/apps.py PayrollConfig.ready()
"""

from django.db.models.signals import pre_save, post_save, post_delete
from django.dispatch import receiver

from .models import DeductionEntry

_TRACKED_FIELDS = (
    'category', 'total_amount', 'currency',
    'split_months', 'start_year', 'start_month', 'note', 'recoverable_id',
)


@receiver(pre_save, sender=DeductionEntry)
def _snapshot_deduction_entry(sender, instance, **kwargs):
    """Stash the pre-change field values on the instance for the post_save diff."""
    instance._audit_before = None
    if instance.pk:
        try:
            prior = DeductionEntry.objects.get(pk=instance.pk)
            instance._audit_before = {f: getattr(prior, f) for f in _TRACKED_FIELDS}
        except DeductionEntry.DoesNotExist:
            pass


@receiver(post_save, sender=DeductionEntry)
def _audit_deduction_entry_save(sender, instance, created, **kwargs):
    from attendance.audit import log_audit, diff_fields
    from attendance.models import AuditLog

    note = 'Captured via signal — actor unknown (change originated outside profile/admin views).'
    if created:
        log_audit(actor='system', action=AuditLog.ACTION_CREATE, instance=instance, note=note)
        return

    before = getattr(instance, '_audit_before', None)
    if not before:
        return
    after = {f: getattr(instance, f) for f in _TRACKED_FIELDS}
    changes = diff_fields(before, after)
    if changes:
        log_audit(actor='system', action=AuditLog.ACTION_UPDATE, instance=instance,
                  changes=changes, note=note)


@receiver(post_delete, sender=DeductionEntry)
def _audit_deduction_entry_delete(sender, instance, **kwargs):
    from attendance.audit import log_audit
    from attendance.models import AuditLog

    log_audit(
        actor='system', action=AuditLog.ACTION_DELETE, instance=instance,
        note='Captured via signal — actor unknown (change originated outside profile/admin views).',
    )
