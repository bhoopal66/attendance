"""Phase 2 — effective-dated employee assignments.

    python 2026-08-17_apply_assignments.py            # show the diff, change nothing
    python 2026-08-17_apply_assignments.py --apply

    attendance/models.py   append EmployeeAssignment
    attendance/admin.py    append a read-oriented registration

NOTHING READS FROM IT YET. This lands the table, the service and the backfill.
The employee row keeps its `department` / `designation` / `reporting_manager`
fields and every existing screen keeps reading them, so this deploy cannot
change a single figure on a payroll page. Switching the reads over is its own
step, done with the bridge up and one screen at a time.

Written without the device bridge, so anchors are matched exactly first, then
whitespace-tolerantly, and the script REFUSES rather than guesses.
"""
import argparse
import difflib
import io
import os
import re
import sys

MODELS = os.path.join('attendance', 'models.py')
ADMIN = os.path.join('attendance', 'admin.py')

ASSIGNMENT_MODEL = '\n\nclass EmployeeAssignment(models.Model):\n    """Where a person sat, and when. The §1 principle, made concrete.\n\n    Today `department`, `team`, `location`, `designation` and\n    `reporting_manager` are single overwritable fields on the employee row.\n    Change someone\'s manager and the previous manager stops having existed —\n    which makes every historical approval, every past appraisal and every "who\n    signed this off" question unanswerable. This table is the record that\n    survives the change.\n\n    SHAPES ARE COPIED, NOT IMPROVED\n    -------------------------------\n    `department`, `team`, `location` and `designation` are CharFields here\n    because that is exactly what they are on the employee row today, even\n    though Department, Team, Location and DesignationMaster tables exist beside\n    them. Normalising strings onto those master tables is real work with real\n    ambiguity ("Admin" vs "Administration") and it belongs in its own pass with\n    its own reconciliation. Copying the shapes verbatim is what makes the\n    backfill provably lossless: every value that goes in comes back out\n    identical, and that can be asserted row by row.\n\n    OVERLAP\n    -------\n    One assignment per employee may be current at a time, and periods must not\n    overlap. That is enforced in `clean()` and in\n    `services_assignments.open_assignment()`, NOT by a partial unique index —\n    MySQL does not support conditional constraints, so a\n    `UniqueConstraint(condition=...)` would be silently useless on the very\n    database this runs on. What IS enforced at database level is the part MySQL\n    can do: no two assignments for the same person starting on the same day.\n    """\n\n    CHANGE_JOINING = \'joining\'\n    CHANGE_PROMOTION = \'promotion\'\n    CHANGE_TRANSFER = \'transfer\'\n    CHANGE_MANAGER = \'manager_change\'\n    CHANGE_REGRADE = \'regrade\'\n    CHANGE_CORRECTION = \'correction\'\n    CHANGE_REHIRE = \'rehire\'\n    CHANGE_OTHER = \'other\'\n    CHANGE_TYPE_CHOICES = [\n        (CHANGE_JOINING, \'Joining\'),\n        (CHANGE_PROMOTION, \'Promotion\'),\n        (CHANGE_TRANSFER, \'Transfer\'),\n        (CHANGE_MANAGER, \'Manager change\'),\n        (CHANGE_REGRADE, \'Grade change\'),\n        (CHANGE_CORRECTION, \'Correction\'),\n        (CHANGE_REHIRE, \'Rehire\'),\n        (CHANGE_OTHER, \'Other\'),\n    ]\n\n    employee = models.ForeignKey(\n        \'attendance.Employee\', on_delete=models.CASCADE,\n        null=True, blank=True, related_name=\'assignments\')\n    remote_employee = models.ForeignKey(\n        \'attendance.RemoteEmployee\', on_delete=models.CASCADE,\n        null=True, blank=True, related_name=\'assignments\')\n    company = models.ForeignKey(\n        \'attendance.Company\', on_delete=models.PROTECT,\n        null=True, blank=True, related_name=\'assignments\',\n        help_text="The entity as at this assignment. Held here as well as on "\n                  "the employee because moving between entities IS a transfer, "\n                  "and the old row has to keep saying where they used to be.")\n\n    effective_from = models.DateField(db_index=True)\n    effective_to = models.DateField(\n        null=True, blank=True, db_index=True,\n        help_text="Blank means open-ended — this is the arrangement in force. "\n                  "Closed by the next assignment, never by hand.")\n    is_current = models.BooleanField(\n        default=True, db_index=True,\n        help_text="Denormalised for querying. `effective_to IS NULL` is the "\n                  "truth; this is the index that makes \'who is where today\' "\n                  "cheap. services_assignments keeps the two in step.")\n\n    department = models.CharField(max_length=100, blank=True, default=\'\')\n    team = models.CharField(max_length=100, blank=True, default=\'\')\n    location = models.CharField(max_length=100, blank=True, default=\'\')\n    designation = models.CharField(max_length=100, blank=True, default=\'\')\n    grade = models.CharField(\n        max_length=50, blank=True, default=\'\',\n        help_text="New — no grade exists on the employee row today, so this is "\n                  "empty on every backfilled row rather than invented.")\n    job_level = models.CharField(max_length=50, blank=True, default=\'\')\n    cost_centre = models.CharField(max_length=50, blank=True, default=\'\')\n\n    reporting_manager = models.ForeignKey(\n        \'attendance.Employee\', on_delete=models.SET_NULL,\n        null=True, blank=True, related_name=\'managed_assignments\')\n    functional_manager = models.ForeignKey(\n        \'attendance.Employee\', on_delete=models.SET_NULL,\n        null=True, blank=True, related_name=\'functionally_managed_assignments\')\n\n    change_type = models.CharField(\n        max_length=20, choices=CHANGE_TYPE_CHOICES, default=CHANGE_OTHER, db_index=True)\n    reason = models.TextField(\n        blank=True, default=\'\',\n        help_text="Why this changed. Empty on backfilled rows, which is honest: "\n                  "nobody recorded a reason at the time and inventing one would "\n                  "put words in somebody\'s mouth.")\n\n    approved_by = models.CharField(max_length=150, blank=True, default=\'\')\n    approved_at = models.DateTimeField(null=True, blank=True)\n    created_by = models.CharField(max_length=150, blank=True, default=\'\')\n    created_at = models.DateTimeField(auto_now_add=True)\n    updated_at = models.DateTimeField(auto_now=True)\n\n    class Meta:\n        ordering = [\'-effective_from\', \'-id\']\n        verbose_name = \'Employee assignment\'\n        indexes = [\n            models.Index(fields=[\'employee\', \'is_current\']),\n            models.Index(fields=[\'remote_employee\', \'is_current\']),\n            models.Index(fields=[\'employee\', \'effective_from\']),\n        ]\n        constraints = [\n            models.UniqueConstraint(\n                fields=[\'employee\', \'effective_from\'],\n                name=\'uniq_assignment_inhouse_start\'),\n            models.UniqueConstraint(\n                fields=[\'remote_employee\', \'effective_from\'],\n                name=\'uniq_assignment_remote_start\'),\n        ]\n\n    def __str__(self):\n        who = self.employee or self.remote_employee\n        end = self.effective_to.isoformat() if self.effective_to else \'current\'\n        return f\'{who} — {self.designation or self.department or "assignment"} ({self.effective_from} to {end})\'\n\n    @property\n    def person(self):\n        return self.employee or self.remote_employee\n\n    def clean(self):\n        super().clean()\n        if self.employee and self.remote_employee:\n            raise ValidationError(\n                \'An assignment belongs to either an in-house or a remote employee, not both.\')\n        if not self.employee and not self.remote_employee:\n            raise ValidationError(\'An assignment must belong to an employee.\')\n        if self.effective_to and self.effective_from and self.effective_to < self.effective_from:\n            raise ValidationError({\'effective_to\': \'Cannot end before it starts.\'})\n\n        # Overlap. Enforced here because MySQL cannot express it as a constraint.\n        if self.effective_from:\n            qs = EmployeeAssignment.objects.filter(\n                employee=self.employee, remote_employee=self.remote_employee)\n            if self.pk:\n                qs = qs.exclude(pk=self.pk)\n            end = self.effective_to\n            for other in qs:\n                o_end = other.effective_to\n                starts_before_other_ends = (o_end is None or self.effective_from <= o_end)\n                other_starts_before_this_ends = (end is None or other.effective_from <= end)\n                if starts_before_other_ends and other_starts_before_this_ends:\n                    raise ValidationError(\n                        \'Overlaps an existing assignment (%s to %s). Close that one first.\'\n                        % (other.effective_from, o_end or \'current\'))\n'

ADMIN_BLOCK = """

# --- Phase 2: effective-dated assignments -------------------------------------
# Read-oriented on purpose. Assignments are opened through
# services_assignments.open_assignment(), which closes the previous period in
# the same transaction. Hand-editing a row here would leave a gap or an overlap
# with nothing to catch it, so the list is browsable and the rows are not.
from attendance.models import EmployeeAssignment as _EmployeeAssignment


@admin.register(_EmployeeAssignment)
class EmployeeAssignmentAdmin(admin.ModelAdmin):
    list_display = ('person', 'effective_from', 'effective_to', 'is_current',
                    'change_type', 'department', 'designation', 'reporting_manager')
    list_filter = ('is_current', 'change_type', 'department')
    search_fields = ('employee__name', 'remote_employee__name',
                     'designation', 'department')
    date_hierarchy = 'effective_from'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
"""


def refuse(msg):
    print('ERROR: ' + msg)
    print('The files are not in the state this patch expects. NOTHING CHANGED.')
    print('Send me the file and I will re-cut the patch against it.')
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    opts = ap.parse_args()

    for path in (MODELS, ADMIN):
        if not os.path.exists(path):
            return refuse('%s not found — run this from the repo root.' % path)

    raw_m = open(MODELS, 'rb').read()
    crlf_m = b'\r\n' in raw_m
    models_src = raw_m.decode('utf-8').replace('\r\n', '\n')

    raw_a = open(ADMIN, 'rb').read()
    crlf_a = b'\r\n' in raw_a
    admin_src = raw_a.decode('utf-8').replace('\r\n', '\n')

    done_m = 'class EmployeeAssignment(models.Model)' in models_src
    done_a = 'class EmployeeAssignmentAdmin' in admin_src
    if done_m and done_a:
        print('Already applied — EmployeeAssignment and its admin are both present.')
        print('Nothing changed. This is the expected result of a second run.')
        return 0

    if 'class Company(models.Model)' not in models_src:
        return refuse('Company is not in models.py yet — run '
                      '2026-08-17_apply_company.py first. The assignment table '
                      'carries a company FK and cannot be created before it.')

    new_models = models_src if done_m else (models_src.rstrip('\n') + '\n' + ASSIGNMENT_MODEL)

    if done_a:
        new_admin = admin_src
    else:
        if not re.search(r'^\s*from django\.contrib import admin', admin_src, re.M):
            return refuse('attendance/admin.py does not import django.contrib.admin as expected.')
        new_admin = admin_src.rstrip('\n') + '\n' + ADMIN_BLOCK

    for path, before, after in ((MODELS, models_src, new_models), (ADMIN, admin_src, new_admin)):
        if before == after:
            continue
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
    print('  copy 0057_employee_assignment.py -> attendance/migrations/')
    print('  copy services_assignments.py     -> attendance/')
    print('  copy backfill_assignments.py     -> attendance/management/commands/')
    print('  python manage.py makemigrations --check --dry-run attendance')
    print('  python manage.py migrate attendance')
    print('  python manage.py backfill_assignments        # report first')
    return 0


if __name__ == '__main__':
    sys.exit(main())
