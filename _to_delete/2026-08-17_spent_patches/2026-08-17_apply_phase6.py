"""Phase 6 — leave policy versions, the leave ledger, and return to work.

    python 2026-08-17_apply_phase6.py            # show the diff, change nothing
    python 2026-08-17_apply_phase6.py --apply

    attendance/models.py   append LeaveType, LeavePolicy, LeavePolicyVersion,
                           LeaveLedgerEntry, EmployeeReturnToWork
    attendance/admin.py    append registrations (policies editable, ledger not)

Requires Phase 5 — LeaveLedgerEntry and EmployeeReturnToWork both extend
PersonScopedModel.

NOTHING READS THESE YET. `services_leave_earnings` keeps its module constants
and every screen keeps showing the computed balance. The ledger is introduced
alongside, with a reconcile step, precisely so the two can be compared before
anything switches over.
"""
import argparse
import difflib
import io
import os
import re
import sys

MODELS = os.path.join('attendance', 'models.py')
ADMIN = os.path.join('attendance', 'admin.py')

PHASE6_MODELS = '\n\nclass LeaveType(models.Model):\n    """Configurable leave types (§28).\n\n    LANDED, NOT WIRED. `LeaveRequest.leave_type` keeps its hard-coded choices\n    for now: repointing a field that every leave screen, the payroll engine and\n    two reports already read is a migration of live data, not a model addition,\n    and it belongs in its own pass. This table is what that pass will read.\n    """\n    code = models.CharField(max_length=30, unique=True)\n    name = models.CharField(max_length=80)\n    is_paid = models.BooleanField(default=True)\n    consumes_annual_entitlement = models.BooleanField(\n        default=False,\n        help_text=\'Whether taking this draws down the annual leave balance. \'\n                  \'Sick leave does not; annual leave does. Getting this wrong \'\n                  \'is how a balance quietly runs out.\')\n    requires_document = models.BooleanField(default=False)\n    max_days_per_year = models.PositiveIntegerField(null=True, blank=True)\n    is_active = models.BooleanField(default=True)\n    created_at = models.DateTimeField(auto_now_add=True)\n\n    class Meta:\n        ordering = [\'name\']\n\n    def __str__(self):\n        return f\'{self.name} ({self.code})\'\n\n\nclass LeavePolicy(models.Model):\n    """A named entitlement rule set — §29 forbids hard-coding these.\n\n    Scoped by jurisdiction, company and employee category so MOHRE staff and\n    offshore staff can be governed differently, which is the whole reason the\n    jurisdiction field exists. A policy with everything blank is the fallback.\n    """\n    name = models.CharField(max_length=120)\n    leave_type_code = models.CharField(\n        max_length=30, default=\'annual\',\n        help_text="Which leave this governs. A string, not an FK to LeaveType, "\n                  "so a policy can be written before the type table is wired in.")\n    labour_jurisdiction = models.CharField(\n        max_length=20, blank=True, default=\'\',\n        help_text=\'Blank means it applies regardless of jurisdiction.\')\n    company = models.ForeignKey(\'attendance.Company\', on_delete=models.CASCADE,\n                                null=True, blank=True, related_name=\'leave_policies\')\n    employee_category = models.CharField(max_length=40, blank=True, default=\'\')\n    is_active = models.BooleanField(default=True)\n    notes = models.TextField(blank=True, default=\'\')\n    created_at = models.DateTimeField(auto_now_add=True)\n\n    class Meta:\n        ordering = [\'leave_type_code\', \'name\']\n        verbose_name_plural = \'Leave policies\'\n\n    def __str__(self):\n        bits = [self.labour_jurisdiction or \'any jurisdiction\',\n                self.company.code if self.company else \'all entities\']\n        return f\'{self.name} ({", ".join(bits)})\'\n\n\nclass LeavePolicyVersion(models.Model):\n    """The numbers, effective-dated (§29).\n\n    Statutory rules change. When they do, a new VERSION is added with a start\n    date — the old one is not edited. That is what lets a settlement computed in\n    2025 still be reproducible in 2027 under the rule that actually applied.\n    """\n    policy = models.ForeignKey(LeavePolicy, on_delete=models.CASCADE, related_name=\'versions\')\n    effective_from = models.DateField(db_index=True)\n    effective_to = models.DateField(null=True, blank=True)\n\n    min_months_for_entitlement = models.DecimalField(\n        max_digits=5, decimal_places=2, default=6,\n        help_text=\'Below this, entitlement is zero — a rule, not a rounding.\')\n    short_service_days_per_month = models.DecimalField(\n        max_digits=5, decimal_places=2, default=2,\n        help_text=\'Days earned per month between the minimum and one year.\')\n    full_days_per_year = models.DecimalField(max_digits=5, decimal_places=2, default=30)\n\n    accrual_basis = models.CharField(\n        max_length=20, default=\'monthly\',\n        help_text="\'monthly\' accrues pro-rata; \'anniversary\' credits only on "\n                  "completed years. bhoopal chose monthly on 17 Aug 2026 (D8).")\n    pay_percentage = models.DecimalField(\n        max_digits=5, decimal_places=2, default=50,\n        help_text=\'Percentage of the wage paid while on this leave. 50 per D7.\')\n    divisor_basis = models.CharField(\n        max_length=20, default=\'period_days\',\n        help_text="\'period_days\' divides by days in the pay period, matching the "\n                  "payroll engine; \'fixed_30\' uses the UAE leave-salary "\n                  "convention. They differ by about 3% in a 31-day month, so "\n                  "this is a real choice and not a formatting preference.")\n    max_carry_forward_days = models.DecimalField(max_digits=5, decimal_places=2,\n                                                 null=True, blank=True)\n    carry_forward_expires_after_months = models.PositiveIntegerField(null=True, blank=True)\n    encashment_allowed = models.BooleanField(default=True)\n    encashment_basis = models.CharField(\n        max_length=20, default=\'gross\',\n        help_text="\'gross\' or \'basic\'. D7 chose gross at the pay percentage; "\n                  "Article 29 sets basic-at-100% as the floor for termination.")\n\n    source_reference = models.CharField(\n        max_length=200, blank=True, default=\'\',\n        help_text=\'Where this rule comes from — e.g. "Federal Decree-Law 33/2021 \'\n                  \'Art. 29" or "Board minute 12 Aug 2026". An unsourced statutory \'\n                  \'number is impossible to defend later.\')\n    approved_by = models.CharField(max_length=150, blank=True, default=\'\')\n    created_at = models.DateTimeField(auto_now_add=True)\n\n    class Meta:\n        ordering = [\'-effective_from\']\n        constraints = [\n            models.UniqueConstraint(fields=[\'policy\', \'effective_from\'],\n                                    name=\'uniq_policy_version_start\'),\n        ]\n\n    def __str__(self):\n        return f\'{self.policy.name} from {self.effective_from}\'\n\n\nclass LeaveLedgerEntry(PersonScopedModel):\n    """Every movement in a leave balance (§30).\n\n    A single `balance = 18` field cannot answer "why 18", cannot be audited, and\n    cannot be corrected without destroying what it was before. This is the\n    ledger: opening balance, accruals, days taken, adjustments, carry-forward,\n    encashment, expiry, reversals — each with a date, a reason and a source.\n\n    `balance_after` is stored rather than recomputed on read. A running balance\n    that is derived every time changes retrospectively whenever an old row is\n    touched, so a payslip printed last month would no longer reproduce. Stored,\n    it is a statement of what the balance WAS at that point.\n    """\n    KIND_OPENING = \'opening\'\n    KIND_ACCRUAL = \'accrual\'\n    KIND_TAKEN = \'taken\'\n    KIND_ADJUSTMENT = \'adjustment\'\n    KIND_CARRY_FORWARD = \'carry_forward\'\n    KIND_ENCASHMENT = \'encashment\'\n    KIND_EXPIRY = \'expiry\'\n    KIND_REVERSAL = \'reversal\'\n    KIND_CHOICES = [\n        (KIND_OPENING, \'Opening balance\'),\n        (KIND_ACCRUAL, \'Accrual\'),\n        (KIND_TAKEN, \'Leave taken\'),\n        (KIND_ADJUSTMENT, \'Manual adjustment\'),\n        (KIND_CARRY_FORWARD, \'Carry forward\'),\n        (KIND_ENCASHMENT, \'Encashment\'),\n        (KIND_EXPIRY, \'Expiry\'),\n        (KIND_REVERSAL, \'Reversal\'),\n    ]\n\n    leave_type_code = models.CharField(max_length=30, default=\'annual\', db_index=True)\n    entry_date = models.DateField(db_index=True)\n    kind = models.CharField(max_length=20, choices=KIND_CHOICES, db_index=True)\n    days = models.DecimalField(\n        max_digits=7, decimal_places=2,\n        help_text=\'POSITIVE credits the balance, NEGATIVE consumes it. One \'\n                  \'signed column rather than debit/credit pairs, because two \'\n                  \'columns invite a row with both filled in.\')\n    balance_after = models.DecimalField(max_digits=8, decimal_places=2)\n\n    description = models.CharField(max_length=200, blank=True, default=\'\')\n    reason = models.TextField(\n        blank=True, default=\'\',\n        help_text=\'Required for manual adjustments — enforced in the service, \'\n                  \'because an unexplained balance change is the thing an audit \'\n                  \'goes looking for first.\')\n    source_model = models.CharField(max_length=60, blank=True, default=\'\')\n    source_id = models.CharField(max_length=40, blank=True, default=\'\')\n    dedupe_key = models.CharField(max_length=190, unique=True)\n\n    approved_by = models.CharField(max_length=150, blank=True, default=\'\')\n    created_by = models.CharField(max_length=150, blank=True, default=\'\')\n    created_at = models.DateTimeField(auto_now_add=True)\n\n    class Meta:\n        ordering = [\'entry_date\', \'id\']\n        verbose_name_plural = \'Leave ledger entries\'\n        indexes = [\n            models.Index(fields=[\'employee\', \'leave_type_code\', \'entry_date\']),\n            models.Index(fields=[\'remote_employee\', \'leave_type_code\', \'entry_date\']),\n        ]\n\n    def __str__(self):\n        return f\'{self.entry_date} {self.get_kind_display()} {self.days:+} -> {self.balance_after}\'\n\n\nclass EmployeeReturnToWork(PersonScopedModel):\n    """Coming back from leave (§31).\n\n    Exists because of one specific failure: returning from annual leave was\n    being handled by editing the joining date, which destroyed length of\n    service, gratuity and every anniversary calculation at once. §97 names it.\n    THIS RECORD IS WHAT CHANGES; the joining date is never touched.\n    """\n    leave_type_code = models.CharField(max_length=30, blank=True, default=\'\')\n    leave_start = models.DateField(null=True, blank=True)\n    expected_return = models.DateField(null=True, blank=True)\n    actual_return = models.DateField(null=True, blank=True)\n    delay_days = models.IntegerField(\n        null=True, blank=True,\n        help_text=\'Actual minus expected. Stored, not derived, so a later change \'\n                  \'to either date cannot silently rewrite a delay that was \'\n                  \'already actioned.\')\n    delay_authorised = models.BooleanField(default=False)\n    supporting_document = models.ForeignKey(\n        \'attendance.EmployeeDocument\', on_delete=models.SET_NULL, null=True,\n        blank=True, related_name=\'return_to_work_records\')\n    payroll_effective_date = models.DateField(\n        null=True, blank=True,\n        help_text=\'When payroll starts paying them again. Deliberately separate \'\n                  \'from actual_return and from the joining date — §97 forbids \'\n                  \'mixing the legal employment date with the payroll one.\')\n    attendance_effective_date = models.DateField(null=True, blank=True)\n    notes = models.TextField(blank=True, default=\'\')\n    approved_by = models.CharField(max_length=150, blank=True, default=\'\')\n    created_by = models.CharField(max_length=150, blank=True, default=\'\')\n    created_at = models.DateTimeField(auto_now_add=True)\n\n    class Meta:\n        ordering = [\'-actual_return\', \'-id\']\n        verbose_name_plural = \'Employee return to work\'\n\n    def __str__(self):\n        return f\'{self.person} returned {self.actual_return or "(not yet)"}\'\n'

ADMIN_BLOCK = """

# --- Phase 6: leave policy and ledger -----------------------------------------
from attendance.models import (
    EmployeeReturnToWork as _EmployeeReturnToWork,
    LeaveLedgerEntry as _LeaveLedgerEntry,
    LeavePolicy as _LeavePolicy,
    LeavePolicyVersion as _LeavePolicyVersion,
    LeaveType as _LeaveType,
)


class _LeavePolicyVersionInline(admin.TabularInline):
    model = _LeavePolicyVersion
    extra = 0
    # A version is added, never edited: editing the numbers on a version that
    # has already been paid against rewrites history silently. Add a new one
    # with a later effective_from instead.
    readonly_fields = ('effective_from', 'source_reference')


@admin.register(_LeavePolicy)
class LeavePolicyAdmin(admin.ModelAdmin):
    list_display = ('name', 'leave_type_code', 'labour_jurisdiction', 'company',
                    'is_active', 'version_count')
    list_filter = ('leave_type_code', 'labour_jurisdiction', 'is_active')
    inlines = [_LeavePolicyVersionInline]

    def version_count(self, obj):
        return obj.versions.count()
    version_count.short_description = 'Versions'


@admin.register(_LeavePolicyVersion)
class LeavePolicyVersionAdmin(admin.ModelAdmin):
    list_display = ('policy', 'effective_from', 'effective_to', 'full_days_per_year',
                    'pay_percentage', 'divisor_basis', 'source_reference')
    list_filter = ('accrual_basis', 'divisor_basis', 'encashment_basis')


@admin.register(_LeaveLedgerEntry)
class LeaveLedgerEntryAdmin(admin.ModelAdmin):
    # READ ONLY. Every row states the balance it left behind; editing one makes
    # every later row assert a balance that never existed. Corrections go on the
    # end as a dated adjustment, through services_leave_ledger.post().
    list_display = ('entry_date', 'person', 'leave_type_code', 'kind', 'days',
                    'balance_after', 'description', 'created_by')
    list_filter = ('kind', 'leave_type_code')
    search_fields = ('employee__name', 'remote_employee__name', 'description', 'reason')
    date_hierarchy = 'entry_date'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(_EmployeeReturnToWork)
class EmployeeReturnToWorkAdmin(admin.ModelAdmin):
    list_display = ('person', 'leave_type_code', 'leave_start', 'expected_return',
                    'actual_return', 'delay_days', 'delay_authorised',
                    'payroll_effective_date')
    list_filter = ('delay_authorised', 'leave_type_code')
    search_fields = ('employee__name', 'remote_employee__name')


@admin.register(_LeaveType)
class LeaveTypeAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'is_paid', 'consumes_annual_entitlement',
                    'requires_document', 'max_days_per_year', 'is_active')
    list_filter = ('is_paid', 'consumes_annual_entitlement', 'is_active')
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

    done_m = 'class LeaveLedgerEntry(' in models_src
    done_a = 'class LeaveLedgerEntryAdmin' in admin_src
    if done_m and done_a:
        print('Already applied — Phase 6 models and admin are both present.')
        return 0
    if 'class PersonScopedModel' not in models_src:
        return refuse('PersonScopedModel is missing — run Phase 5 first.')

    new_models = models_src if done_m else (models_src.rstrip('\n') + '\n' + PHASE6_MODELS)
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
    print('  copy 0061_leave_policy_and_ledger.py -> attendance/migrations/')
    print('  copy services_leave_ledger.py        -> attendance/')
    print('  copy seed_leave_policy.py            -> attendance/management/commands/')
    print('  copy backfill_leave_ledger.py        -> attendance/management/commands/')
    print('  python manage.py makemigrations --check --dry-run attendance')
    print('  python manage.py migrate attendance')
    print('  python manage.py seed_leave_policy           # report first')
    print('  python manage.py backfill_leave_ledger       # report first')
    return 0


if __name__ == '__main__':
    sys.exit(main())
