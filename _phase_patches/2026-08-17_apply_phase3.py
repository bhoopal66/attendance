"""Phase 3 — approval engine and employee timeline.

    python 2026-08-17_apply_phase3.py            # show the diff, change nothing
    python 2026-08-17_apply_phase3.py --apply

    attendance/models.py   append EmployeeTimelineEvent, ApprovalChain,
                           ApprovalChainStep, ApprovalRequest, ApprovalStep
    attendance/admin.py    append registrations (chains editable, decisions not)

Requires Phase 2 — the assignment applier is the vertical slice that proves the
engine reaches real history.

No existing screen changes. Approvals are only used by code that opts in.
"""
import argparse
import difflib
import io
import os
import re
import sys

MODELS = os.path.join('attendance', 'models.py')
ADMIN = os.path.join('attendance', 'admin.py')

PHASE3_MODELS = '\n\nclass EmployeeTimelineEvent(models.Model):\n    """One line in the story of an employee. §46 calls this mandatory.\n\n    Joined · confirmed · salary revised · promoted · manager changed · leave\n    taken · warning issued · visa renewed · resigned. One chronology, filterable,\n    that answers "what happened to this person" without opening six screens.\n\n    DEDUPE, AND WHY IT IS A STRING\n    ------------------------------\n    Events are written by backfills and by live code, and both run more than\n    once. Without a key, re-running a backfill doubles everyone\'s history —\n    which is worse than no timeline, because it looks authoritative.\n\n    The key is a single unique CharField rather than a composite unique index\n    across (employee, remote_employee, type, source, date). MySQL treats NULLs\n    in a unique index as distinct, and exactly one of those two FKs is always\n    NULL, so the composite version would permit duplicates on the very database\n    this runs on. 190 characters because that is the utf8mb4 index limit.\n\n    SOURCE IS A STRING PAIR, NOT A GENERIC FOREIGN KEY\n    --------------------------------------------------\n    Same choice AuditLog already made in this codebase: `source_model` and\n    `source_id` as plain fields, so the attendance app never takes a hard\n    dependency on payroll to render a payroll event.\n    """\n\n    CATEGORY_CHOICES = [\n        (\'employment\', \'Employment\'),\n        (\'salary\', \'Salary\'),\n        (\'leave\', \'Leave\'),\n        (\'attendance\', \'Attendance\'),\n        (\'promotion\', \'Promotion\'),\n        (\'warning\', \'Warning\'),\n        (\'document\', \'Document\'),\n        (\'payroll\', \'Payroll\'),\n        (\'performance\', \'Performance\'),\n        (\'compliance\', \'Compliance\'),\n        (\'other\', \'Other\'),\n    ]\n\n    employee = models.ForeignKey(\n        \'attendance.Employee\', on_delete=models.CASCADE,\n        null=True, blank=True, related_name=\'timeline_events\')\n    remote_employee = models.ForeignKey(\n        \'attendance.RemoteEmployee\', on_delete=models.CASCADE,\n        null=True, blank=True, related_name=\'timeline_events\')\n\n    event_date = models.DateField(db_index=True)\n    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES,\n                                default=\'other\', db_index=True)\n    event_type = models.CharField(max_length=50, db_index=True)\n    title = models.CharField(max_length=200)\n    detail = models.TextField(blank=True, default=\'\')\n\n    source_model = models.CharField(max_length=60, blank=True, default=\'\')\n    source_id = models.CharField(max_length=40, blank=True, default=\'\')\n\n    dedupe_key = models.CharField(max_length=190, unique=True)\n    created_by = models.CharField(max_length=150, blank=True, default=\'\')\n    created_at = models.DateTimeField(auto_now_add=True)\n\n    class Meta:\n        ordering = [\'-event_date\', \'-id\']\n        indexes = [\n            models.Index(fields=[\'employee\', \'event_date\']),\n            models.Index(fields=[\'remote_employee\', \'event_date\']),\n            models.Index(fields=[\'category\', \'event_date\']),\n        ]\n\n    def __str__(self):\n        return f\'{self.event_date} — {self.title}\'\n\n    @property\n    def person(self):\n        return self.employee or self.remote_employee\n\n\nclass ApprovalChain(models.Model):\n    """Who has to say yes to a given kind of transaction, in what order.\n\n    Configurable per company, with a fallback: a chain whose `company` is blank\n    applies to every entity that has no chain of its own. That is what makes\n    adding a second entity cheap — it inherits until somebody says otherwise.\n\n    §58 also wants chains varying by department, amount and grade. Not built:\n    those conditions have no callers yet, and a configuration surface nobody\n    fills in is a place for stale rules to hide.\n    """\n    request_type = models.CharField(max_length=40, db_index=True)\n    company = models.ForeignKey(\n        \'attendance.Company\', on_delete=models.CASCADE,\n        null=True, blank=True, related_name=\'approval_chains\',\n        help_text="Blank means this chain is the fallback for every entity.")\n    description = models.CharField(max_length=200, blank=True, default=\'\')\n    is_active = models.BooleanField(default=True)\n    created_at = models.DateTimeField(auto_now_add=True)\n\n    class Meta:\n        ordering = [\'request_type\', \'company__code\']\n        constraints = [\n            models.UniqueConstraint(fields=[\'request_type\', \'company\'],\n                                    name=\'uniq_chain_per_type_company\'),\n        ]\n\n    def __str__(self):\n        return f\'{self.request_type} — {self.company.code if self.company else "all entities"}\'\n\n\nclass ApprovalChainStep(models.Model):\n    """One rung of a chain: a role that must approve, and where it sits."""\n    chain = models.ForeignKey(ApprovalChain, on_delete=models.CASCADE, related_name=\'steps\')\n    sequence = models.PositiveIntegerField(help_text=\'1 = first approver.\')\n    role_required = models.CharField(\n        max_length=30,\n        help_text="Matches UserProfile.role. Kept as a string rather than a "\n                  "choices list so a role added later does not need a migration "\n                  "here as well.")\n    label = models.CharField(max_length=100, blank=True, default=\'\')\n\n    class Meta:\n        ordering = [\'sequence\']\n        constraints = [\n            models.UniqueConstraint(fields=[\'chain\', \'sequence\'], name=\'uniq_step_per_chain\'),\n        ]\n\n    def __str__(self):\n        return f\'{self.chain.request_type} #{self.sequence} {self.role_required}\'\n\n\nclass ApprovalRequest(models.Model):\n    """A transaction waiting for permission, with the values as submitted.\n\n    §89: the payload is a SNAPSHOT. If the employee master changes while this\n    sits pending, the approver still sees exactly what was submitted. Anything\n    else means people approve one thing and a different thing takes effect.\n    """\n    STATUS_PENDING = \'pending\'\n    STATUS_APPROVED = \'approved\'\n    STATUS_REJECTED = \'rejected\'\n    STATUS_CANCELLED = \'cancelled\'\n    STATUS_CHOICES = [\n        (STATUS_PENDING, \'Pending\'),\n        (STATUS_APPROVED, \'Approved\'),\n        (STATUS_REJECTED, \'Rejected\'),\n        (STATUS_CANCELLED, \'Cancelled\'),\n    ]\n\n    request_type = models.CharField(max_length=40, db_index=True)\n    employee = models.ForeignKey(\n        \'attendance.Employee\', on_delete=models.CASCADE,\n        null=True, blank=True, related_name=\'approval_requests\')\n    remote_employee = models.ForeignKey(\n        \'attendance.RemoteEmployee\', on_delete=models.CASCADE,\n        null=True, blank=True, related_name=\'approval_requests\')\n    company = models.ForeignKey(\n        \'attendance.Company\', on_delete=models.SET_NULL,\n        null=True, blank=True, related_name=\'approval_requests\')\n\n    payload = models.JSONField(\n        default=dict,\n        help_text="The submitted values, frozen. Never recomputed from the "\n                  "employee record — that is the whole point (§89).")\n    summary = models.CharField(\n        max_length=300, blank=True, default=\'\',\n        help_text="Human-readable one-liner, also frozen at submit time.")\n    effective_date = models.DateField(null=True, blank=True)\n    reason = models.TextField(blank=True, default=\'\')\n\n    status = models.CharField(max_length=12, choices=STATUS_CHOICES,\n                              default=STATUS_PENDING, db_index=True)\n    submitted_by = models.CharField(max_length=150, blank=True, default=\'\')\n    submitted_at = models.DateTimeField(auto_now_add=True)\n    decided_at = models.DateTimeField(null=True, blank=True)\n    applied_at = models.DateTimeField(\n        null=True, blank=True,\n        help_text="When the approved change actually took effect. Separate from "\n                  "decided_at because approval and application can fail apart.")\n    apply_error = models.TextField(blank=True, default=\'\')\n\n    class Meta:\n        ordering = [\'-submitted_at\']\n        indexes = [models.Index(fields=[\'status\', \'request_type\'])]\n\n    def __str__(self):\n        return f\'{self.request_type} for {self.person} ({self.status})\'\n\n    @property\n    def person(self):\n        return self.employee or self.remote_employee\n\n    @property\n    def pending_step(self):\n        return self.steps.filter(decision=ApprovalStep.DECISION_PENDING).order_by(\'sequence\').first()\n\n\nclass ApprovalStep(models.Model):\n    """One approver\'s answer on one request."""\n    DECISION_PENDING = \'pending\'\n    DECISION_APPROVED = \'approved\'\n    DECISION_REJECTED = \'rejected\'\n    DECISION_CHOICES = [\n        (DECISION_PENDING, \'Pending\'),\n        (DECISION_APPROVED, \'Approved\'),\n        (DECISION_REJECTED, \'Rejected\'),\n    ]\n\n    request = models.ForeignKey(ApprovalRequest, on_delete=models.CASCADE, related_name=\'steps\')\n    sequence = models.PositiveIntegerField()\n    role_required = models.CharField(max_length=30)\n    label = models.CharField(max_length=100, blank=True, default=\'\')\n    decision = models.CharField(max_length=12, choices=DECISION_CHOICES,\n                                default=DECISION_PENDING, db_index=True)\n    decided_by = models.CharField(max_length=150, blank=True, default=\'\')\n    decided_at = models.DateTimeField(null=True, blank=True)\n    comments = models.TextField(blank=True, default=\'\')\n\n    class Meta:\n        ordering = [\'sequence\']\n        constraints = [\n            models.UniqueConstraint(fields=[\'request\', \'sequence\'],\n                                    name=\'uniq_step_per_request\'),\n        ]\n\n    def __str__(self):\n        return f\'#{self.sequence} {self.role_required} — {self.decision}\'\n'

ADMIN_BLOCK = """

# --- Phase 3: approvals and timeline ------------------------------------------
from attendance.models import (
    ApprovalChain as _ApprovalChain,
    ApprovalChainStep as _ApprovalChainStep,
    ApprovalRequest as _ApprovalRequest,
    ApprovalStep as _ApprovalStep,
    EmployeeTimelineEvent as _EmployeeTimelineEvent,
)


class _ChainStepInline(admin.TabularInline):
    model = _ApprovalChainStep
    extra = 1


@admin.register(_ApprovalChain)
class ApprovalChainAdmin(admin.ModelAdmin):
    # Chains ARE editable — that is the configuration surface. A chain with no
    # steps is refused at submit time rather than silently auto-approving.
    list_display = ('request_type', 'company', 'description', 'is_active', 'step_count')
    list_filter = ('request_type', 'is_active')
    inlines = [_ChainStepInline]

    def step_count(self, obj):
        return obj.steps.count()
    step_count.short_description = 'Steps'


class _ApprovalStepInline(admin.TabularInline):
    model = _ApprovalStep
    extra = 0
    can_delete = False
    readonly_fields = ('sequence', 'role_required', 'label', 'decision',
                       'decided_by', 'decided_at', 'comments')


@admin.register(_ApprovalRequest)
class ApprovalRequestAdmin(admin.ModelAdmin):
    # Read-only on purpose. Decisions go through services_approvals.decide(),
    # which enforces order and applies the change. Editing a decision here
    # would record an approval that never applied anything.
    list_display = ('request_type', 'person', 'summary', 'effective_date',
                    'status', 'submitted_by', 'submitted_at', 'applied_at')
    list_filter = ('status', 'request_type')
    search_fields = ('employee__name', 'remote_employee__name', 'summary')
    inlines = [_ApprovalStepInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(_EmployeeTimelineEvent)
class EmployeeTimelineEventAdmin(admin.ModelAdmin):
    list_display = ('event_date', 'person', 'category', 'event_type', 'title')
    list_filter = ('category', 'event_type')
    search_fields = ('employee__name', 'remote_employee__name', 'title', 'detail')
    date_hierarchy = 'event_date'

    def has_change_permission(self, request, obj=None):
        return False
"""


def refuse(msg):
    print('ERROR: ' + msg)
    print('The files are not in the state this patch expects. NOTHING CHANGED.')
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    opts = ap.parse_args()

    for path in (MODELS, ADMIN):
        if not os.path.exists(path):
            return refuse('%s not found — run this from the repo root.' % path)

    raw_m = open(MODELS, 'rb').read(); crlf_m = b'\r\n' in raw_m
    models_src = raw_m.decode('utf-8').replace('\r\n', '\n')
    raw_a = open(ADMIN, 'rb').read(); crlf_a = b'\r\n' in raw_a
    admin_src = raw_a.decode('utf-8').replace('\r\n', '\n')

    done_m = 'class EmployeeTimelineEvent' in models_src
    done_a = 'class ApprovalChainAdmin' in admin_src
    if done_m and done_a:
        print('Already applied — Phase 3 models and admin are both present.')
        return 0
    if 'class EmployeeAssignment(models.Model)' not in models_src:
        return refuse('EmployeeAssignment is missing — run '
                      '2026-08-17_apply_assignments.py (Phase 2) first.')

    new_models = models_src if done_m else (models_src.rstrip('\n') + '\n' + PHASE3_MODELS)
    if done_a:
        new_admin = admin_src
    else:
        if not re.search(r'^\s*from django\.contrib import admin', admin_src, re.M):
            return refuse('attendance/admin.py does not import django.contrib.admin as expected.')
        new_admin = admin_src.rstrip('\n') + '\n' + ADMIN_BLOCK

    for path, before, after in ((MODELS, models_src, new_models), (ADMIN, admin_src, new_admin)):
        if before != after:
            print('')
            sys.stdout.writelines(difflib.unified_diff(
                before.splitlines(True), after.splitlines(True),
                fromfile=path, tofile=path + ' (patched)', n=2))

    if not opts.apply:
        print('')
        print('DRY RUN — nothing was written. Re-run with --apply if the diff is right.')
        return 0

    for path, after, crlf in ((MODELS, new_models, crlf_m), (ADMIN, new_admin, crlf_a)):
        out = after.replace('\n', '\r\n') if crlf else after
        with io.open(path, 'wb') as fh:
            fh.write(out.encode('utf-8'))
        print('PATCHED %s (%s)' % (path, 'CRLF' if crlf else 'LF'))

    print('')
    print('Next:')
    print('  copy 0058_timeline_and_approvals.py -> attendance/migrations/')
    print('  copy services_timeline.py           -> attendance/')
    print('  copy services_approvals.py          -> attendance/')
    print('  copy backfill_timeline.py           -> attendance/management/commands/')
    print('  python manage.py makemigrations --check --dry-run attendance')
    print('  python manage.py migrate attendance')
    print('  python manage.py backfill_timeline           # report first')
    print('')
    print('Then configure at least one approval chain in the admin, or nothing')
    print('can be submitted — which is deliberate.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
