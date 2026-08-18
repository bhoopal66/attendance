"""Phase 5 — identity and compliance completion.

    python 2026-08-17_apply_phase5.py            # show the diff, change nothing
    python 2026-08-17_apply_phase5.py --apply

    attendance/models.py   MOHRE identifier block + passport type/place on
                           BaseEmployee; passport fields on Employee; and seven
                           new tables (visa history, dependents, insurance,
                           medical fitness, education, qualifications, previous
                           employment) on a shared PersonScopedModel base
    attendance/admin.py    registrations for all seven

Requires Phase 1a (labour_jurisdiction must already be on BaseEmployee).
No existing screen changes — every table is new and nothing reads from them yet.
"""
import argparse
import difflib
import io
import os
import re
import sys

MODELS = os.path.join('attendance', 'models.py')
ADMIN = os.path.join('attendance', 'admin.py')

TABLES = '\n\nclass PersonScopedModel(models.Model):\n    """Abstract base for the dual-employee pattern.\n\n    Seven Phase 5 tables need the same two nullable foreign keys and the same\n    guard: exactly one of them, never both, never neither. Repeating that seven\n    times is seven chances to leave the guard off one of them — which is how a\n    record ends up belonging to nobody and disappearing from every report that\n    joins through an employee.\n    """\n    employee = models.ForeignKey(\n        \'attendance.Employee\', on_delete=models.CASCADE,\n        null=True, blank=True, related_name=\'%(class)ss\')\n    remote_employee = models.ForeignKey(\n        \'attendance.RemoteEmployee\', on_delete=models.CASCADE,\n        null=True, blank=True, related_name=\'%(class)ss\')\n\n    class Meta:\n        abstract = True\n\n    @property\n    def person(self):\n        return self.employee or self.remote_employee\n\n    def clean(self):\n        super().clean()\n        if self.employee and self.remote_employee:\n            raise ValidationError(\n                \'This record belongs to either an in-house or a remote employee, not both.\')\n        if not self.employee and not self.remote_employee:\n            raise ValidationError(\'This record must belong to an employee.\')\n\n\nclass EmployeeVisa(PersonScopedModel):\n    """UAE residency visas, kept as a history rather than overwritten (§11).\n\n    Today a renewal replaces the previous visa and the old permit number stops\n    having existed. That matters: a cancelled visa\'s file number is what a\n    government query is made against months later.\n\n    WHERE EXPIRY LIVES, AND WHY IT IS HERE\n    --------------------------------------\n    An expiry date is intrinsic to the visa, so it belongs on the visa. But\n    `services_compliance.watchlist()` reads expiry from `EmployeeDocument`, and\n    two tables holding the same date is how the dashboard ends up disagreeing\n    with the record. `document` links this row to the scan that the watchlist\n    already tracks so the pair can be reconciled — and until the watchlist is\n    pointed at this table, THE DOCUMENT REMAINS THE ONE THE ALERTS COME FROM.\n    That is a deliberate, temporary duplication, written down rather than\n    discovered later.\n    """\n    VISA_STATUS_CHOICES = [\n        (\'active\', \'Active\'),\n        (\'expired\', \'Expired\'),\n        (\'cancelled\', \'Cancelled\'),\n        (\'under_process\', \'Under process\'),\n    ]\n    SPONSOR_TYPE_CHOICES = [\n        (\'company\', \'Company\'),\n        (\'spouse\', \'Spouse\'),\n        (\'parent\', \'Parent\'),\n        (\'self\', \'Self / investor\'),\n        (\'free_zone\', \'Free zone authority\'),\n        (\'other\', \'Other\'),\n    ]\n\n    uid_number = models.CharField(max_length=40, blank=True, default=\'\',\n                                  help_text=\'Unified ID — stays with the person across visas.\')\n    visa_file_number = models.CharField(max_length=60, blank=True, default=\'\')\n    residence_permit_number = models.CharField(max_length=60, blank=True, default=\'\')\n    visa_type = models.CharField(max_length=30, blank=True, default=\'\')\n    sponsor = models.CharField(max_length=150, blank=True, default=\'\')\n    sponsor_type = models.CharField(max_length=20, choices=SPONSOR_TYPE_CHOICES,\n                                    blank=True, default=\'\')\n    place_of_issue = models.CharField(max_length=100, blank=True, default=\'\')\n    issue_date = models.DateField(null=True, blank=True)\n    expiry_date = models.DateField(null=True, blank=True, db_index=True)\n    status = models.CharField(max_length=20, choices=VISA_STATUS_CHOICES,\n                              default=\'active\', db_index=True)\n    is_current = models.BooleanField(default=True, db_index=True)\n    inside_country = models.BooleanField(\n        null=True, blank=True,\n        help_text=\'Issued in-country or out. Null means not recorded — which is \'\n                  \'different from "outside".\')\n    cancellation_date = models.DateField(null=True, blank=True)\n    cancellation_reference = models.CharField(max_length=80, blank=True, default=\'\')\n    document = models.ForeignKey(\n        \'attendance.EmployeeDocument\', on_delete=models.SET_NULL, null=True, blank=True,\n        related_name=\'visas\', help_text=\'The scan the compliance watchlist tracks.\')\n    notes = models.TextField(blank=True, default=\'\')\n    created_by = models.CharField(max_length=150, blank=True, default=\'\')\n    created_at = models.DateTimeField(auto_now_add=True)\n    updated_at = models.DateTimeField(auto_now=True)\n\n    class Meta:\n        ordering = [\'-issue_date\', \'-id\']\n        indexes = [models.Index(fields=[\'employee\', \'is_current\']),\n                   models.Index(fields=[\'expiry_date\'])]\n\n    def __str__(self):\n        return f\'{self.person} — visa {self.residence_permit_number or self.visa_file_number or "?"}\'\n\n\nclass EmployeeDependent(PersonScopedModel):\n    """Spouse and children — sponsorship, documents and cover (§15)."""\n    RELATIONSHIP_CHOICES = [\n        (\'spouse\', \'Spouse\'), (\'son\', \'Son\'), (\'daughter\', \'Daughter\'),\n        (\'father\', \'Father\'), (\'mother\', \'Mother\'), (\'other\', \'Other\'),\n    ]\n    name = models.CharField(max_length=150)\n    relationship = models.CharField(max_length=20, choices=RELATIONSHIP_CHOICES)\n    date_of_birth = models.DateField(null=True, blank=True)\n    gender = models.CharField(max_length=10, blank=True, default=\'\')\n    nationality = models.CharField(max_length=80, blank=True, default=\'\')\n    passport_number = models.CharField(max_length=60, blank=True, default=\'\')\n    passport_expiry = models.DateField(null=True, blank=True)\n    emirates_id = models.CharField(max_length=40, blank=True, default=\'\')\n    emirates_id_expiry = models.DateField(null=True, blank=True)\n    visa_expiry = models.DateField(null=True, blank=True)\n    sponsored_by_company = models.BooleanField(\n        default=False,\n        help_text=\'Whether the company sponsors this dependent. Drives cost and \'\n                  \'renewal responsibility, so it is not the same as "has a visa".\')\n    insurance_covered = models.BooleanField(default=False)\n    notes = models.TextField(blank=True, default=\'\')\n    created_at = models.DateTimeField(auto_now_add=True)\n\n    class Meta:\n        ordering = [\'relationship\', \'name\']\n\n    def __str__(self):\n        return f\'{self.name} ({self.get_relationship_display()})\'\n\n\nclass EmployeeInsurance(PersonScopedModel):\n    """Health insurance, with the cost split (§14)."""\n    provider = models.CharField(max_length=120)\n    policy_number = models.CharField(max_length=80, blank=True, default=\'\')\n    member_number = models.CharField(max_length=80, blank=True, default=\'\')\n    category = models.CharField(max_length=60, blank=True, default=\'\')\n    network = models.CharField(max_length=60, blank=True, default=\'\')\n    coverage_start = models.DateField(null=True, blank=True)\n    coverage_end = models.DateField(null=True, blank=True, db_index=True)\n    covers_employee = models.BooleanField(default=True)\n    covers_dependents = models.BooleanField(default=False)\n    total_cost = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)\n    employer_contribution = models.DecimalField(max_digits=10, decimal_places=2,\n                                                null=True, blank=True)\n    employee_contribution = models.DecimalField(\n        max_digits=10, decimal_places=2, null=True, blank=True,\n        help_text=\'Any employee share. NOT derived from total minus employer — a \'\n                  \'derived figure would silently absorb a data-entry error into \'\n                  \'a payroll deduction.\')\n    currency = models.CharField(max_length=3, default=\'AED\')\n    is_current = models.BooleanField(default=True, db_index=True)\n    document = models.ForeignKey(\n        \'attendance.EmployeeDocument\', on_delete=models.SET_NULL, null=True, blank=True,\n        related_name=\'insurance_policies\')\n    notes = models.TextField(blank=True, default=\'\')\n    created_at = models.DateTimeField(auto_now_add=True)\n\n    class Meta:\n        ordering = [\'-coverage_start\', \'-id\']\n\n    def __str__(self):\n        return f\'{self.provider} — {self.policy_number or "no policy no."}\'\n\n\nclass EmployeeMedicalFitness(PersonScopedModel):\n    """Medical fitness tests, which recur — hence a table, not fields (§13)."""\n    RESULT_CHOICES = [(\'fit\', \'Fit\'), (\'unfit\', \'Unfit\'),\n                      (\'pending\', \'Pending\'), (\'referred\', \'Referred\')]\n    application_number = models.CharField(max_length=80, blank=True, default=\'\')\n    test_date = models.DateField(null=True, blank=True)\n    centre = models.CharField(max_length=150, blank=True, default=\'\')\n    result = models.CharField(max_length=20, choices=RESULT_CHOICES,\n                              blank=True, default=\'\')\n    certificate_number = models.CharField(max_length=80, blank=True, default=\'\')\n    expiry_date = models.DateField(null=True, blank=True, db_index=True)\n    document = models.ForeignKey(\n        \'attendance.EmployeeDocument\', on_delete=models.SET_NULL, null=True, blank=True,\n        related_name=\'medical_tests\')\n    notes = models.TextField(blank=True, default=\'\')\n    created_at = models.DateTimeField(auto_now_add=True)\n\n    class Meta:\n        ordering = [\'-test_date\', \'-id\']\n        verbose_name_plural = \'Employee medical fitness records\'\n\n    def __str__(self):\n        return f\'{self.person} — medical {self.test_date or "undated"}\'\n\n\nclass EmployeeEducation(PersonScopedModel):\n    """Academic qualifications, with attestation state (§16).\n\n    Attestation is three booleans rather than one status, because MOFA and the\n    UAE embassy are separate steps that fail separately, and "attested" without\n    saying by whom is not an answer anyone can act on.\n    """\n    qualification = models.CharField(max_length=120)\n    degree = models.CharField(max_length=120, blank=True, default=\'\')\n    specialisation = models.CharField(max_length=120, blank=True, default=\'\')\n    institution = models.CharField(max_length=200, blank=True, default=\'\')\n    country = models.CharField(max_length=80, blank=True, default=\'\')\n    start_date = models.DateField(null=True, blank=True)\n    completion_date = models.DateField(null=True, blank=True)\n    grade = models.CharField(max_length=40, blank=True, default=\'\')\n    certificate_number = models.CharField(max_length=80, blank=True, default=\'\')\n    attested = models.BooleanField(default=False)\n    mofa_attested = models.BooleanField(default=False)\n    uae_embassy_attested = models.BooleanField(default=False)\n    document = models.ForeignKey(\n        \'attendance.EmployeeDocument\', on_delete=models.SET_NULL, null=True, blank=True,\n        related_name=\'education_records\')\n    created_at = models.DateTimeField(auto_now_add=True)\n\n    class Meta:\n        ordering = [\'-completion_date\', \'-id\']\n\n    def __str__(self):\n        return f\'{self.qualification} — {self.institution or "?"}\'\n\n\nclass EmployeeQualification(PersonScopedModel):\n    """Professional licences and memberships — CA, ACCA, CPA, CFA, medical (§17).\n\n    Separate from EmployeeEducation because these EXPIRE and carry CPD\n    obligations. Folding them into one table would leave every academic degree\n    with a null expiry and every licence with a null grade, and would put a\n    lapsing practising licence in a table nothing checks for renewal.\n    """\n    title = models.CharField(max_length=120)\n    issuing_authority = models.CharField(max_length=150, blank=True, default=\'\')\n    membership_number = models.CharField(max_length=80, blank=True, default=\'\')\n    issue_date = models.DateField(null=True, blank=True)\n    expiry_date = models.DateField(null=True, blank=True, db_index=True)\n    cpd_required = models.BooleanField(default=False)\n    cpd_hours_required = models.PositiveIntegerField(null=True, blank=True)\n    is_current = models.BooleanField(default=True, db_index=True)\n    document = models.ForeignKey(\n        \'attendance.EmployeeDocument\', on_delete=models.SET_NULL, null=True, blank=True,\n        related_name=\'qualifications\')\n    notes = models.TextField(blank=True, default=\'\')\n    created_at = models.DateTimeField(auto_now_add=True)\n\n    class Meta:\n        ordering = [\'-issue_date\', \'-id\']\n\n    def __str__(self):\n        return f\'{self.title} — {self.membership_number or "no member no."}\'\n\n\nclass EmployeePreviousEmployment(PersonScopedModel):\n    """Employment before this one, and whether the reference was actually taken (§18)."""\n    employer = models.CharField(max_length=200)\n    country = models.CharField(max_length=80, blank=True, default=\'\')\n    designation = models.CharField(max_length=120, blank=True, default=\'\')\n    from_date = models.DateField(null=True, blank=True)\n    to_date = models.DateField(null=True, blank=True)\n    last_salary = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)\n    salary_currency = models.CharField(max_length=3, blank=True, default=\'\')\n    reason_for_leaving = models.CharField(max_length=200, blank=True, default=\'\')\n    reference_name = models.CharField(max_length=150, blank=True, default=\'\')\n    reference_contact = models.CharField(max_length=150, blank=True, default=\'\')\n    reference_checked = models.BooleanField(\n        default=False,\n        help_text=\'Whether the reference was TAKEN, not whether a contact was \'\n                  \'recorded. An unchecked reference beside a filled-in phone \'\n                  \'number is exactly the gap an audit asks about.\')\n    reference_checked_by = models.CharField(max_length=150, blank=True, default=\'\')\n    reference_checked_at = models.DateField(null=True, blank=True)\n    notes = models.TextField(blank=True, default=\'\')\n    created_at = models.DateTimeField(auto_now_add=True)\n\n    class Meta:\n        ordering = [\'-to_date\', \'-id\']\n        verbose_name_plural = \'Employee previous employment\'\n\n    def __str__(self):\n        return f\'{self.employer} — {self.designation or "?"}\'\n'
MOHRE_BLOCK = '    # --- MOHRE identifiers (§9) -------------------------------------------\n    # IDENTIFIERS ONLY. The work permit\'s expiry is deliberately NOT here: the\n    # labour card already exists as an EmployeeDocument and the compliance\n    # watchlist reads its expiry. A second expiry date would give the same\n    # renewal two answers, and the one nobody is watching would be the wrong\n    # one. What lives here is the set of numbers a MOHRE query is made against.\n    mohre_person_number = models.CharField(\n        max_length=40, blank=True, default=\'\', db_index=True,\n        help_text=\'MOHRE person number — follows the individual, not the permit.\')\n    labour_card_number = models.CharField(max_length=40, blank=True, default=\'\')\n    work_permit_number = models.CharField(max_length=40, blank=True, default=\'\')\n    work_permit_type = models.CharField(max_length=60, blank=True, default=\'\')\n    work_permit_status = models.CharField(max_length=40, blank=True, default=\'\')\n    establishment_number = models.CharField(\n        max_length=40, blank=True, default=\'\',\n        help_text="The employing establishment\'s MOHRE number. Held per employee "\n                  "as well as per company because an employee can sit under a "\n                  "different establishment from their entity\'s default.")\n    labour_contract_number = models.CharField(max_length=60, blank=True, default=\'\')\n    mohre_job_title = models.CharField(\n        max_length=120, blank=True, default=\'\',\n        help_text=\'Job title AS FILED WITH MOHRE, which is frequently not the \'\n                  \'internal designation. Both are needed: one for the labour \'\n                  \'file, one for the org chart.\')\n    skill_level = models.CharField(max_length=30, blank=True, default=\'\')\n\n'
PASSPORT_FIELDS = "    passport_type = models.CharField(max_length=40, blank=True, default='')\n    passport_place_of_issue = models.CharField(max_length=100, blank=True, default='')\n"

MOHRE_ANCHOR = '    LABOUR_JURISDICTION_CHOICES = ['
PASSPORT_ANCHOR = '    passport_number = models.CharField('

ADMIN_BLOCK = """

# --- Phase 5: identity and compliance -----------------------------------------
from attendance.models import (
    EmployeeDependent as _EmployeeDependent,
    EmployeeEducation as _EmployeeEducation,
    EmployeeInsurance as _EmployeeInsurance,
    EmployeeMedicalFitness as _EmployeeMedicalFitness,
    EmployeePreviousEmployment as _EmployeePreviousEmployment,
    EmployeeQualification as _EmployeeQualification,
    EmployeeVisa as _EmployeeVisa,
)


@admin.register(_EmployeeVisa)
class EmployeeVisaAdmin(admin.ModelAdmin):
    # Editable, unlike assignments: a visa row is a record of a document, not a
    # period whose neighbours have to be closed. Renewals should still go
    # through services_identity.renew_visa(), which retires the previous one.
    list_display = ('person', 'residence_permit_number', 'visa_type', 'sponsor',
                    'issue_date', 'expiry_date', 'status', 'is_current')
    list_filter = ('status', 'is_current', 'sponsor_type', 'visa_type')
    search_fields = ('employee__name', 'remote_employee__name', 'uid_number',
                     'visa_file_number', 'residence_permit_number')
    date_hierarchy = 'expiry_date'


@admin.register(_EmployeeDependent)
class EmployeeDependentAdmin(admin.ModelAdmin):
    list_display = ('name', 'person', 'relationship', 'date_of_birth',
                    'sponsored_by_company', 'insurance_covered', 'visa_expiry')
    list_filter = ('relationship', 'sponsored_by_company', 'insurance_covered')
    search_fields = ('name', 'employee__name', 'remote_employee__name', 'passport_number')


@admin.register(_EmployeeInsurance)
class EmployeeInsuranceAdmin(admin.ModelAdmin):
    list_display = ('person', 'provider', 'policy_number', 'category',
                    'coverage_start', 'coverage_end', 'is_current')
    list_filter = ('provider', 'is_current', 'covers_dependents')
    search_fields = ('employee__name', 'remote_employee__name', 'policy_number',
                     'member_number')
    date_hierarchy = 'coverage_end'


@admin.register(_EmployeeMedicalFitness)
class EmployeeMedicalFitnessAdmin(admin.ModelAdmin):
    list_display = ('person', 'test_date', 'centre', 'result',
                    'certificate_number', 'expiry_date')
    list_filter = ('result',)
    search_fields = ('employee__name', 'remote_employee__name',
                     'certificate_number', 'application_number')


@admin.register(_EmployeeEducation)
class EmployeeEducationAdmin(admin.ModelAdmin):
    list_display = ('person', 'qualification', 'institution', 'country',
                    'completion_date', 'attested', 'mofa_attested',
                    'uae_embassy_attested')
    list_filter = ('attested', 'mofa_attested', 'uae_embassy_attested', 'country')
    search_fields = ('employee__name', 'remote_employee__name', 'qualification',
                     'institution')


@admin.register(_EmployeeQualification)
class EmployeeQualificationAdmin(admin.ModelAdmin):
    list_display = ('person', 'title', 'issuing_authority', 'membership_number',
                    'expiry_date', 'cpd_required', 'is_current')
    list_filter = ('is_current', 'cpd_required', 'issuing_authority')
    search_fields = ('employee__name', 'remote_employee__name', 'title',
                     'membership_number')
    date_hierarchy = 'expiry_date'


@admin.register(_EmployeePreviousEmployment)
class EmployeePreviousEmploymentAdmin(admin.ModelAdmin):
    list_display = ('person', 'employer', 'designation', 'from_date', 'to_date',
                    'reference_checked')
    list_filter = ('reference_checked', 'country')
    search_fields = ('employee__name', 'remote_employee__name', 'employer')
"""


def refuse(msg):
    print('ERROR: ' + msg)
    print('The files are not in the state this patch expects. NOTHING CHANGED.')
    return 1


def insert_before(src, anchor_exact, anchor_regex, payload, what):
    if src.count(anchor_exact) == 1:
        return src.replace(anchor_exact, payload + anchor_exact, 1), None
    lines = src.split('\n')
    idx = [i for i, l in enumerate(lines) if re.match(anchor_regex, l)]
    if len(idx) != 1:
        return src, 'expected one %s anchor, found %d' % (what, len(idx))
    lines[idx[0]:idx[0]] = payload.rstrip('\n').split('\n')
    return '\n'.join(lines), None


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

    done_m = 'class EmployeeVisa(' in models_src
    done_a = 'class EmployeeVisaAdmin' in admin_src
    if done_m and done_a:
        print('Already applied — Phase 5 models and admin are both present.')
        return 0
    if 'labour_jurisdiction = models.CharField(' not in models_src:
        return refuse('labour_jurisdiction is missing — run Phase 1a first.')

    new_models = models_src
    if not done_m:
        new_models, err = insert_before(
            new_models, MOHRE_ANCHOR, r'^\s*LABOUR_JURISDICTION_CHOICES = \[',
            MOHRE_BLOCK, 'LABOUR_JURISDICTION_CHOICES')
        if err:
            return refuse(err)
        if 'passport_type = models.CharField(' not in new_models:
            new_models, err = insert_before(
                new_models, PASSPORT_ANCHOR, r'^\s*passport_number = models\.CharField\(',
                PASSPORT_FIELDS, 'passport_number')
            if err:
                return refuse(err)
        new_models = new_models.rstrip('\n') + '\n' + TABLES

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
    print('  copy 0060_identity_and_compliance.py -> attendance/migrations/')
    print('  copy services_identity.py            -> attendance/')
    print('  copy backfill_visas.py               -> attendance/management/commands/')
    print('  python manage.py makemigrations --check --dry-run attendance')
    print('  python manage.py migrate attendance')
    print('  python manage.py backfill_visas              # report first')
    return 0


if __name__ == '__main__':
    sys.exit(main())
