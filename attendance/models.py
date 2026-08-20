import datetime
import logging
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models

logger = logging.getLogger('attendance')

tcr_id_validator = RegexValidator(
    regex=r'^TCR\d+$',
    message='TCR ID must be in the format TCR followed by digits (e.g. TCR1000224).'
)


# =============================================================================
# Master Data / Lookup Tables  (Phase 2 — additive, no existing fields changed)
# =============================================================================

class Department(models.Model):
    """Master list of departments (e.g. Sales, Admin, Operations).
    Replaces the two-choice CharField on BaseEmployee in a future FK migration.
    """
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, default='')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Team(models.Model):
    """Master list of teams. A team may optionally belong to a department."""
    name = models.CharField(max_length=100, unique=True)
    department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='teams',
    )
    description = models.TextField(blank=True, default='')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Location(models.Model):
    """Master list of office / work locations (e.g. Dubai HQ, Abu Dhabi Branch)."""
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, default='')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class DesignationMaster(models.Model):
    """Master list of job designations / titles (e.g. Sales Executive, HR Manager)."""
    title = models.CharField(max_length=150, unique=True)
    department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='designations',
    )
    description = models.TextField(blank=True, default='')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['title']
        verbose_name = 'Designation'
        verbose_name_plural = 'Designations'

    def __str__(self):
        return self.title


# =============================================================================
# End Master Data Tables
# =============================================================================


class BaseEmployee(models.Model):
    """Abstract base model for shared fields between Employee and RemoteEmployee."""
    DEPARTMENT_CHOICES = [
        ('Sales', 'Sales'),
        ('Admin', 'Admin'),
    ]

    tcr_id = models.CharField(
        max_length=20,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
        validators=[tcr_id_validator],
        help_text="Unique company employee ID (e.g. TCR1000224). Same ID links in-house and remote records for the same person."
    )

    name = models.CharField(max_length=100)
    email = models.EmailField(null=True, blank=True, db_index=True)
    phone = models.CharField(max_length=20, null=True, blank=True)
    department = models.CharField(
        max_length=100, choices=DEPARTMENT_CHOICES, null=True, blank=True
    )
    location = models.CharField(
        max_length=100, null=True, blank=True,
        help_text="Office location (e.g., 'Dubai HQ', 'Abu Dhabi Branch')"
    )
    team = models.CharField(
        max_length=100, null=True, blank=True,
        help_text="Team name (e.g., 'Sales', 'Engineering', 'Support')"
    )

    is_active = models.BooleanField(
        default=True, db_index=True,
        help_text="Uncheck when employee leaves the company"
    )
    joining_date = models.DateField(null=True, blank=True)
    leaving_date = models.DateField(
        null=True, blank=True,
        help_text="Date when employee left the company"
    )

    portal_password = models.CharField(
        max_length=128, null=True, blank=True,
        help_text="Hashed password for employee portal login"
    )

    CURRENCY_CHOICES = [
        ('AED', 'AED'),
        ('INR', 'INR'),
        ('NPR', 'NPR'),
    ]
    currency = models.CharField(
        max_length=3,
        choices=CURRENCY_CHOICES,
        default='AED',
        help_text="Currency for salary payment"
    )

    PAYROLL_TYPE_CHOICES = [
        ('attendance', 'Attendance Based'),
        ('performance', 'Performance Based'),
    ]
    payroll_type = models.CharField(
        max_length=20,
        choices=PAYROLL_TYPE_CHOICES,
        default='attendance',
        help_text="Attendance-based applies late/leave deductions; Performance-based has no such deductions"
    )

    is_fixed_salary = models.BooleanField(
        default=False,
        help_text="Fixed salary employees: punch-in alone counts as Present (no punch-out or duration thresholds required)"
    )

    salary_cycle_start_day = models.PositiveSmallIntegerField(
        default=21,
        help_text="Pay period start day. 21 = 21st prev month to 20th current; 1 = calendar month (1st to last day)."
    )

    VISA_PROVIDER_CHOICES = [
        ('Jumbo', 'Jumbo'),
        ('OnTime', 'OnTime'),
        ('Taamul', 'Taamul'),
    ]
    company = models.ForeignKey(
        'attendance.Company', on_delete=models.PROTECT,
        null=True, blank=True, related_name='%(class)ss',
        help_text="Which legal entity employs this person. NULLABLE for now: "
                  "the column lands first, every existing employee is assigned "
                  "to a default entity by the seed_company command, and only "
                  "then is it made required. Making it required in the same "
                  "migration that creates it would fail on the first row of a "
                  "live payroll database. "
                  "on_delete=PROTECT because deleting a company out from under "
                  "44 payroll records should be refused, not cascaded.",
    )

    # --- MOHRE identifiers (§9) -------------------------------------------
    # IDENTIFIERS ONLY. The work permit's expiry is deliberately NOT here: the
    # labour card already exists as an EmployeeDocument and the compliance
    # watchlist reads its expiry. A second expiry date would give the same
    # renewal two answers, and the one nobody is watching would be the wrong
    # one. What lives here is the set of numbers a MOHRE query is made against.
    mohre_person_number = models.CharField(
        max_length=40, blank=True, default='', db_index=True,
        help_text='MOHRE person number — follows the individual, not the permit.')
    labour_card_number = models.CharField(max_length=40, blank=True, default='')
    work_permit_number = models.CharField(max_length=40, blank=True, default='')
    work_permit_type = models.CharField(max_length=60, blank=True, default='')
    work_permit_status = models.CharField(max_length=40, blank=True, default='')
    establishment_number = models.CharField(
        max_length=40, blank=True, default='',
        help_text="The employing establishment's MOHRE number. Held per employee "
                  "as well as per company because an employee can sit under a "
                  "different establishment from their entity's default.")
    labour_contract_number = models.CharField(max_length=60, blank=True, default='')
    mohre_job_title = models.CharField(
        max_length=120, blank=True, default='',
        help_text='Job title AS FILED WITH MOHRE, which is frequently not the '
                  'internal designation. Both are needed: one for the labour '
                  'file, one for the org chart.')
    skill_level = models.CharField(max_length=30, blank=True, default='')

    LABOUR_JURISDICTION_CHOICES = [
        ('', '— not recorded —'),
        ('mohre', 'MOHRE — UAE mainland'),
        ('uae_own_visa', 'UAE — own / spouse visa'),
        ('offshore', 'Non-UAE / offshore'),
        ('other', 'Other'),
    ]
    labour_jurisdiction = models.CharField(
        max_length=20, choices=LABOUR_JURISDICTION_CHOICES, blank=True,
        default='', db_index=True,
        help_text="Which employment regime this person actually falls under. "
                  "NOT a formality: MOHRE staff are covered by UAE labour law "
                  "(Article 29 leave pay, gratuity, WPS); offshore staff working "
                  "from India or Nepal are not covered by any of it and are "
                  "governed by their contract instead. "
                  "Deliberately BLANK by default rather than defaulting to "
                  "MOHRE — a wrong jurisdiction stamped on the whole workforce "
                  "is worse than an empty one, because it looks like a decision "
                  "somebody made. Statutory rules are NOT yet gated on this "
                  "field (bhoopal, 17 Aug 2026): it records the truth now so "
                  "gating can be switched on later without re-deriving who is "
                  "who, one employee at a time, from memory.",
    )

    visa_provider = models.CharField(
        max_length=20,
        choices=VISA_PROVIDER_CHOICES,
        null=True,
        blank=True,
        help_text="Manpower visa provider; leave blank for own-visa employees"
    )

    # ── Compliance block ──────────────────────────────────────────────────
    # The identity documents themselves (Emirates ID, passport, UAE visa,
    # labour card, medical insurance — number, issue date, expiry, scan and HR
    # verification) live in EmployeeDocument and are NOT duplicated here.
    # One number, one place. What follows is only what the document records
    # cannot express.

    VISA_TYPE_CHOICES = [
        ('employment', 'Employment Visa'),
        ('investor',   'Investor / Partner Visa'),
        ('golden',     'Golden Visa'),
        ('dependent',  'Dependent Visa'),
        ('freelance',  'Freelance Permit'),
        ('other',      'Other'),
    ]
    visa_type = models.CharField(
        max_length=20, choices=VISA_TYPE_CHOICES, blank=True, default='',
        help_text="Category of UAE visa. The number and expiry live on the "
                  "employee's UAE Visa document record, not here.",
    )

    CONTRACT_LIMITED = 'limited'
    CONTRACT_UNLIMITED = 'unlimited'
    CONTRACT_TYPE_CHOICES = [
        (CONTRACT_LIMITED,   'Limited'),
        (CONTRACT_UNLIMITED, 'Unlimited (legacy)'),
    ]
    contract_type = models.CharField(
        max_length=20, choices=CONTRACT_TYPE_CHOICES, blank=True, default='',
        help_text="Gratuity computation basis. Deliberately blank by default: "
                  "an unrecorded contract type must read as unknown, never as "
                  "a guess, because the guess would change an end-of-service "
                  "figure without anyone deciding to.",
    )

    probation_end_date = models.DateField(
        null=True, blank=True,
        help_text="Leave blank to use joining date + 90 days. Set it only when "
                  "the real end date differs — a stored value always wins, and "
                  "is shown as confirmed rather than inferred.",
    )

    taamul_connect_user_id = models.CharField(
        max_length=100, blank=True, default='', db_index=True,
        help_text="TaamulConnect user ID — API sync key and the handle used to "
                  "provision or revoke access.",
    )

    commission_plan_code = models.CharField(
        max_length=30, blank=True, default='', db_index=True,
        help_text="Bridge to the DSA commission engine. Not applicable to "
                  "Admin-department staff.",
    )

    # Assigned partner banks are a link table in the payroll app
    # (payroll.EmployeePartnerBank), not a field here: the relation carries its
    # own data (which bank is primary, when it was assigned) and Bank lives in
    # payroll, which already imports attendance rather than the reverse.

    compliance_reviewed_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When this employee's compliance record was last confirmed "
                  "correct by a human.",
    )
    compliance_reviewed_by = models.CharField(
        max_length=150, blank=True, default='',
        help_text="Who confirmed it.",
    )

    # ── End Compliance block ──────────────────────────────────────────────

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    PROBATION_DAYS = 90
    PROBATION_REVIEW_DAY = 75
    COMPLIANCE_REVIEW_INTERVAL_DAYS = 90

    class Meta:
        abstract = True

    def __str__(self):
        return self.name

    # ── Compliance derived values ─────────────────────────────────────────
    # Every one of these is COMPUTED. Nothing below writes a date into the
    # database, because a written default is indistinguishable from a date
    # somebody checked.

    @property
    def probation_end(self):
        """The stored probation end date, or joining_date + 90 days."""
        if self.probation_end_date:
            return self.probation_end_date
        if self.joining_date:
            return self.joining_date + datetime.timedelta(days=self.PROBATION_DAYS)
        return None

    @property
    def probation_is_inferred(self):
        """True when the date above was derived rather than recorded."""
        return not self.probation_end_date and self.joining_date is not None

    @property
    def probation_review_due(self):
        """Day 75 — when the review has to happen to beat the deadline."""
        if not self.joining_date:
            return None
        if self.probation_end_date:
            return self.probation_end_date - datetime.timedelta(
                days=self.PROBATION_DAYS - self.PROBATION_REVIEW_DAY)
        return self.joining_date + datetime.timedelta(days=self.PROBATION_REVIEW_DAY)

    def probation_state(self, today=None):
        """'none' | 'review_due' | 'in_probation' | 'passed'."""
        today = today or datetime.date.today()
        end = self.probation_end
        if end is None:
            return 'none'
        if today > end:
            return 'passed'
        due = self.probation_review_due
        if due is not None and today >= due:
            return 'review_due'
        return 'in_probation'

    @property
    def compliance_review_due_date(self):
        if not self.compliance_reviewed_at:
            return None
        return (self.compliance_reviewed_at.date()
                + datetime.timedelta(days=self.COMPLIANCE_REVIEW_INTERVAL_DAYS))

    def compliance_review_state(self, today=None):
        """'never' | 'due' | 'current'.

        'never' is not the same as 'due'. A record nobody has ever checked is a
        different problem from one that was checked and has gone stale, and the
        watchlist should be able to say which.
        """
        today = today or datetime.date.today()
        due = self.compliance_review_due_date
        if due is None:
            return 'never'
        return 'due' if today >= due else 'current'

    def clean(self):
        super().clean()
        if self.leaving_date and self.joining_date and self.leaving_date < self.joining_date:
            raise ValidationError({
                'leaving_date': "Leaving date cannot be before joining date."
            })
        if self.probation_end_date and self.joining_date and \
                self.probation_end_date < self.joining_date:
            raise ValidationError({
                'probation_end_date': "Probation cannot end before the joining date."
            })


class Holiday(models.Model):
    """Custom holidays (other than Sundays) that apply to all employees."""
    date = models.DateField(unique=True)
    name = models.CharField(max_length=100)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date']

    def __str__(self):
        return f"{self.name} ({self.date})"


class SpecialShiftPeriod(models.Model):
    """
    A named date range during which employees work different hours / thresholds.
    e.g., Ramadan working hours (9:00 AM - 4:00 PM) for a specific date range.

    During this period, the special shift overrides each employee's normal shift
    when computing attendance status in the report.

    For remote employees, call-minute thresholds can be overridden per day type.
    """
    name = models.CharField(max_length=100, help_text="e.g., 'Ramadan 2025'")
    start_date = models.DateField()
    end_date = models.DateField()
    shift_start = models.TimeField(help_text="Weekday (Mon-Fri) shift start time during this period")
    shift_end = models.TimeField(help_text="Weekday (Mon-Fri) shift end time during this period")
    sat_shift_start = models.TimeField(
        null=True, blank=True,
        help_text="Saturday shift start (leave blank to keep the regular 10:00 AM)"
    )
    sat_shift_end = models.TimeField(
        null=True, blank=True,
        help_text="Saturday shift end (leave blank to keep the regular 2:00 PM)"
    )
    # Remote employee call-minute thresholds (leave blank to keep defaults)
    remote_weekday_half_day_mins = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Mon-Thu: minimum talk minutes for half day (default 45)"
    )
    remote_weekday_present_mins = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Mon-Thu: minimum talk minutes for present (default 90)"
    )
    remote_friday_half_day_mins = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Friday: minimum talk minutes for half day (default 30)"
    )
    remote_friday_present_mins = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Friday: minimum talk minutes for present (default 60)"
    )
    remote_saturday_half_day_mins = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Saturday: minimum talk minutes for half day (default 21)"
    )
    remote_saturday_present_mins = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Saturday: minimum talk minutes for present (default 45)"
    )
    notes = models.TextField(blank=True, help_text="Optional notes about this period")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-start_date']

    def __str__(self):
        return f"{self.name} ({self.start_date} – {self.end_date})"

    def clean(self):
        super().clean()
        if self.end_date and self.start_date and self.end_date < self.start_date:
            raise ValidationError({'end_date': "End date cannot be before start date."})
        if self.shift_end and self.shift_start and self.shift_end <= self.shift_start:
            raise ValidationError({'shift_end': "Shift end must be after shift start."})
        if bool(self.sat_shift_start) != bool(self.sat_shift_end):
            raise ValidationError("Both Saturday shift start and end must be set together.")
        # Validate remote thresholds: half_day must be less than present
        for label, hd, pr in [
            ('Mon-Thu', self.remote_weekday_half_day_mins, self.remote_weekday_present_mins),
            ('Friday', self.remote_friday_half_day_mins, self.remote_friday_present_mins),
            ('Saturday', self.remote_saturday_half_day_mins, self.remote_saturday_present_mins),
        ]:
            if hd is not None and pr is not None and hd >= pr:
                raise ValidationError(f"{label} remote: half-day minutes must be less than present minutes.")
            if bool(hd is not None) != bool(pr is not None):
                raise ValidationError(f"{label} remote: both half-day and present thresholds must be set together.")


class Employee(BaseEmployee):
    """In-house employee tracked via attendance machine."""
    person_id = models.CharField(max_length=50, db_index=True)

    salary = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    designation = models.CharField(max_length=100, null=True, blank=True)

    shift_start = models.TimeField(
        null=True, blank=True,
        help_text="Expected arrival time for Mon-Fri (e.g., 09:30). Saturday is always 10:00-14:00."
    )
    shift_end = models.TimeField(
        null=True, blank=True,
        help_text="Expected departure time for Mon-Fri (e.g., 18:30). Saturday is always 10:00-14:00."
    )

    # ── Phase 3 — 360° Profile Fields ──────────────────────────────────────────

    # Org hierarchy
    reporting_manager = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='direct_reports',
        help_text="Direct reporting manager for this employee",
    )

    # Personal Information
    GENDER_CHOICES = [
        ('M', 'Male'),
        ('F', 'Female'),
        ('O', 'Other'),
    ]
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES, null=True, blank=True)
    BLOOD_GROUP_CHOICES = [
        ('A+', 'A+'), ('A-', 'A-'),
        ('B+', 'B+'), ('B-', 'B-'),
        ('O+', 'O+'), ('O-', 'O-'),
        ('AB+', 'AB+'), ('AB-', 'AB-'),
    ]
    blood_group = models.CharField(max_length=4, choices=BLOOD_GROUP_CHOICES, null=True, blank=True)

    # Emergency Contact
    emergency_contact_name = models.CharField(max_length=100, null=True, blank=True)
    emergency_contact_phone = models.CharField(max_length=20, null=True, blank=True)

    # Identity Documents
    national_id = models.CharField(
        max_length=50, null=True, blank=True,
        help_text="Emirates ID / Aadhaar / National ID number",
    )
    passport_type = models.CharField(max_length=40, blank=True, default='')
    passport_place_of_issue = models.CharField(max_length=100, blank=True, default='')
    passport_number = models.CharField(max_length=30, null=True, blank=True)

    # Bank Details
    bank_name = models.CharField(max_length=100, null=True, blank=True)
    bank_account_number = models.CharField(max_length=50, null=True, blank=True)
    bank_routing_code = models.CharField(
        max_length=30, null=True, blank=True,
        help_text="IFSC (India) / IBAN / Routing code depending on bank country",
    )

    # Profile Photo
    profile_photo = models.ImageField(
        upload_to='employee_photos/',
        null=True, blank=True,
        help_text="Employee passport-style photo",
    )

    # Employment Lifecycle
    EMPLOYMENT_STATUS_CHOICES = [
        ('active',      'Active'),
        ('on_notice',   'On Notice'),
        ('relieved',    'Relieved'),
        ('absconded',   'Absconded'),
    ]
    employment_status = models.CharField(
        max_length=20,
        choices=EMPLOYMENT_STATUS_CHOICES,
        default='active',
        db_index=True,
        help_text="Current employment lifecycle stage",
    )
    notice_date = models.DateField(
        null=True, blank=True,
        help_text="Date notice period began",
    )
    relieving_date = models.DateField(
        null=True, blank=True,
        help_text="Official last working day / relieving date",
    )

    # Onboarding Checklist
    onboarding_checklist = models.JSONField(
        default=dict, blank=True,
        help_text="Dict of checklist item keys → True/False completion state",
    )

    # ── End Phase 3 Fields ─────────────────────────────────────────────────────

    class Meta:
        unique_together = ('person_id', 'name')

    def __str__(self):
        return f"{self.name} ({self.person_id})"

    # ── Computed Properties ────────────────────────────────────────────────────

    PROFILE_TRACKABLE_FIELDS = [
        'email', 'phone', 'date_of_birth', 'gender', 'blood_group',
        'emergency_contact_name', 'emergency_contact_phone',
        'national_id', 'passport_number',
        'bank_name', 'bank_account_number',
        'joining_date', 'designation', 'department', 'location', 'team',
        'reporting_manager_id',
        'profile_photo',
    ]

    @property
    def profile_completeness(self):
        """Return integer 0–100 representing % of trackable fields filled."""
        filled = sum(
            1 for field in self.PROFILE_TRACKABLE_FIELDS
            if getattr(self, field, None)
        )
        return round(filled / len(self.PROFILE_TRACKABLE_FIELDS) * 100)

    @property
    def employment_status_display_class(self):
        return {
            'active':    'status-active',
            'on_notice': 'status-notice',
            'relieved':  'status-relieved',
            'absconded': 'status-absconded',
        }.get(self.employment_status, 'status-active')


class AttendanceRecord(models.Model):
    """Daily attendance record for in-house employees."""
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE)
    date = models.DateField(db_index=True)
    first_in = models.TimeField(null=True, blank=True)
    last_out = models.TimeField(null=True, blank=True)
    work_duration = models.DurationField(null=True, blank=True)

    is_work_from_home = models.BooleanField(
        default=False,
        help_text="Mark this day as Work From Home (counts as full day present)"
    )
    is_paid_leave = models.BooleanField(
        default=False,
        help_text="Mark this day as Paid Leave (shows as blue, not deducted from salary)"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('employee', 'date')
        indexes = [
            models.Index(fields=['employee', 'date']),
        ]

    def __str__(self):
        return f"{self.employee.name} - {self.date}"


class MonthlySummary(models.Model):
    """Monthly attendance summary for in-house employees."""
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE)
    year = models.IntegerField()
    month = models.IntegerField()
    working_days = models.IntegerField(default=0)
    leave_days = models.IntegerField(default=0)
    late_days = models.IntegerField(default=0)
    half_days = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('employee', 'year', 'month')
        verbose_name_plural = 'Monthly Summaries'

    def __str__(self):
        return f"{self.employee.name} - {self.year}/{self.month}"


class ShiftHistory(models.Model):
    """
    Tracks shift timing changes for employees over time.

    Note: Shift history applies to Monday-Friday only. Saturday is always 10:00 AM - 2:00 PM
    for all employees, regardless of their regular shift timings or shift history.
    """
    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name='shift_history'
    )
    shift_start = models.TimeField(
        help_text="Expected arrival time for Mon-Fri (e.g., 09:30)"
    )
    shift_end = models.TimeField(
        help_text="Expected departure time for Mon-Fri (e.g., 18:30)"
    )
    effective_from = models.DateField(
        help_text="Date from which this shift timing applies"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'Shift Histories'
        ordering = ['-effective_from']

    def __str__(self):
        return f"{self.employee.name}: {self.shift_start}-{self.shift_end} (from {self.effective_from})"

    def clean(self):
        super().clean()
        if self.shift_start and self.shift_end and self.shift_start >= self.shift_end:
            raise ValidationError("Shift start must be before shift end.")


# ============================================
# Phase 4 — Employment History (Effective-Dated Change Log)
# ============================================

class EmploymentHistory(models.Model):
    """
    Immutable, effective-dated log of employment changes for in-house employees.

    A row is created automatically whenever the Employment or Bank/Payroll
    section of the employee profile is saved and a tracked field has changed.
    Records are never edited or deleted — they form a permanent audit trail.

    Tracked fields → change_type:
        designation, department, team, location,
        reporting_manager, employment_status, salary
    """

    CHANGE_TYPES = [
        ('designation',       'Designation'),
        ('department',        'Department'),
        ('team',              'Team'),
        ('location',          'Location'),
        ('reporting_manager', 'Reporting Manager'),
        ('employment_status', 'Employment Status'),
        ('salary',            'Salary'),
        ('other',             'Other'),
    ]

    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name='employment_history',
        help_text="The in-house employee this change belongs to",
    )
    change_type = models.CharField(
        max_length=30,
        choices=CHANGE_TYPES,
        db_index=True,
        help_text="Which aspect of employment changed",
    )
    effective_date = models.DateField(
        help_text="Calendar date on which this change took effect",
    )
    previous_value = models.JSONField(
        null=True,
        blank=True,
        help_text="Snapshot of the value before the change (null for first-ever entry)",
    )
    new_value = models.JSONField(
        help_text="Snapshot of the value after the change",
    )
    reason = models.TextField(
        blank=True,
        help_text="Optional business reason or comment for this change",
    )
    changed_by = models.CharField(
        max_length=150,
        help_text="Username of the admin who made the change",
    )
    changed_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Server timestamp when the change was recorded (immutable)",
    )

    class Meta:
        ordering = ['-effective_date', '-changed_at']
        verbose_name = 'Employment History'
        verbose_name_plural = 'Employment History'
        indexes = [
            models.Index(fields=['employee', '-effective_date']),
            models.Index(fields=['change_type']),
        ]

    def __str__(self):
        return (
            f"{self.employee.name} — {self.get_change_type_display()} "
            f"on {self.effective_date}"
        )


# ============================================
# Phase 5 — Salary Structure (Component Breakdown + Effective-Dated History)
# ============================================

class SalaryStructure(models.Model):
    """
    Effective-dated salary component breakdown for in-house employees.

    Each row represents a salary revision.  When a new revision is saved
    the previous 'approved' row is superseded automatically by the view layer.
    Employee.salary is kept in sync with the gross of the latest approved row
    so payroll code continues to read a single authoritative field.

    Components
    ----------
    basic, housing, transport, phone, other_allowance  → gross = sum of all five

    Immutability
    ------------
    Rows are never edited after creation.  The admin class enforces this.
    """

    STATUS_CHOICES = [
        ('approved',   'Approved'),
        ('superseded', 'Superseded'),
    ]

    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name='salary_structures',
        help_text="In-house employee this salary structure belongs to",
    )
    effective_from = models.DateField(
        help_text="Date from which this salary structure is effective",
    )

    # ── Allowance components ────────────────────────────────────────────────────
    basic = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text="Basic / base pay component",
    )
    housing = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text="Housing allowance",
    )
    transport = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text="Transport allowance",
    )
    phone = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text="Phone / communication allowance",
    )
    other_allowance = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text="Any other allowance not covered above",
    )

    currency = models.CharField(
        max_length=3, default='AED',
        help_text="ISO currency code (e.g. AED, INR, NPR)",
    )
    revision_type = models.CharField(
        max_length=30, blank=True, default='',
        help_text="Annual increment, promotion, market adjustment, correction, "
                  "other. Free text rather than choices so a new category does "
                  "not need a migration; the transaction layer supplies it.")
    approved_by = models.CharField(
        max_length=150, blank=True, default='',
        help_text="Who signed it off. Written by the approval engine, not by "
                  "hand — a name typed into a box is not an approval.")
    approved_at = models.DateTimeField(null=True, blank=True)
    revision_reason = models.TextField(
        blank=True,
        help_text="Business reason for this salary revision (e.g. Annual appraisal)",
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='approved', db_index=True,
        help_text="'approved' = current; 'superseded' = replaced by a newer revision",
    )
    created_by = models.CharField(
        max_length=150,
        help_text="Username of the admin who created this revision",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-effective_from', '-created_at']
        verbose_name = 'Salary Structure'
        verbose_name_plural = 'Salary Structures'
        indexes = [
            models.Index(fields=['employee', '-effective_from'], name='attendance__salary_emp_idx'),
            models.Index(fields=['status'], name='attendance__salary_status_idx'),
        ]

    def __str__(self):
        return (
            f"{self.employee.name} — {self.currency} {self.gross:,.2f} "
            f"(from {self.effective_from}, {self.status})"
        )

    @property
    def gross(self):
        """Total gross salary = sum of all five components."""
        return (
            (self.basic or 0)
            + (self.housing or 0)
            + (self.transport or 0)
            + (self.phone or 0)
            + (self.other_allowance or 0)
        )


# ============================================
# Salary cycle history — company-wide default
# ============================================

class SalaryCycleDefault(models.Model):
    """Company-wide default pay cycle, effective-dated by an exact date.

    An employee follows this timeline unless they have their own
    `SalaryCycleHistory` row — see `attendance.services_salary_cycle`, which
    resolves this and `SalaryCycleHistory` together and CLIPS the period
    either side of `effective_date` so a cycle change never re-pays or skips
    a day: the old cycle's last period ends the day before `effective_date`,
    the new cycle's first period starts exactly on it. With zero rows here,
    resolution falls through to each employee's legacy
    `salary_cycle_start_day` field, so adding this feature changes nothing
    until someone actually records a default.
    """
    cycle_start_day = models.PositiveSmallIntegerField(
        help_text="1 = calendar month (1st to last day); 2-28 = day of the "
                   "previous month the cycle starts on"
    )
    effective_date = models.DateField(
        help_text="Exact date this cycle takes effect (inclusive)"
    )
    note = models.CharField(max_length=255, blank=True)
    created_by = models.CharField(
        max_length=150, blank=True,
        help_text="Username of the admin who created this entry",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-effective_date']
        verbose_name = 'Salary Cycle Default'
        verbose_name_plural = 'Salary Cycle Defaults'
        constraints = [
            models.UniqueConstraint(
                fields=['effective_date'],
                name='uniq_salary_cycle_default_date',
            ),
        ]

    def __str__(self):
        return f"Default: day {self.cycle_start_day} (from {self.effective_date})"

    def clean(self):
        super().clean()
        if self.cycle_start_day is not None and not (1 <= self.cycle_start_day <= 28):
            raise ValidationError({'cycle_start_day': 'Cycle start day must be between 1 and 28.'})


class SalaryCycleGroupDefault(models.Model):
    """Pay cycle for one payroll group, effective-dated by an exact date.

    Sits between `SalaryCycleHistory` (one employee's own override) and
    `SalaryCycleDefault` (everyone) in the resolution order — see
    `attendance.services_salary_cycle._merged_timeline`. A group with its
    own entries governs every employee classified into it (by
    `payroll.services_payroll_engine.classify_employee_section` — department
    / `is_fixed_salary` / `tcr_id` dedup, computed live, not stored) from
    its earliest entry onward, overriding the company default for them
    specifically, unless that particular employee also has their own
    `SalaryCycleHistory` row (which wins over the group in turn).

    `group` values must match `payroll.services_payroll_engine.SECTION_*`
    keys — `sales_perf_method2` is deliberately excluded: it's the same
    remote employees as `sales_perf`, just recalculated for display, so it
    is not a separate pay-cycle population.
    """
    GROUP_CHOICES = [
        ('admin_inhouse', 'Admin (In-house)'),
        ('admin_remote', 'Admin (Remote)'),
        ('sales_fixed', 'Sales (Fixed Salary)'),
        ('sales_perf', 'Sales (Performance)'),
    ]
    group = models.CharField(max_length=32, choices=GROUP_CHOICES)
    cycle_start_day = models.PositiveSmallIntegerField(
        help_text="1 = calendar month (1st to last day); 2-28 = day of the "
                   "previous month the cycle starts on"
    )
    effective_date = models.DateField(
        help_text="Exact date this cycle takes effect (inclusive)"
    )
    note = models.CharField(max_length=255, blank=True)
    created_by = models.CharField(
        max_length=150, blank=True,
        help_text="Username of the admin who created this entry",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-effective_date']
        verbose_name = 'Salary Cycle Group Default'
        verbose_name_plural = 'Salary Cycle Group Defaults'
        constraints = [
            models.UniqueConstraint(
                fields=['group', 'effective_date'],
                name='uniq_salary_cycle_group_date',
            ),
        ]

    def __str__(self):
        return f"{self.get_group_display()}: day {self.cycle_start_day} (from {self.effective_date})"

    def clean(self):
        super().clean()
        if self.cycle_start_day is not None and not (1 <= self.cycle_start_day <= 28):
            raise ValidationError({'cycle_start_day': 'Cycle start day must be between 1 and 28.'})


# ============================================
# Phase 6 — Employer Cost Setup
# ============================================

class EmployerCostSetup(models.Model):
    """
    Effective-dated record of all employer-side costs for a single employee.

    One employee may have multiple rows as costs change over time; the most
    recent row (highest effective_from) is the "current" cost setup.

    Covers both in-house (Employee) and remote (RemoteEmployee) staff —
    exactly one FK should be set; the other must be null.
    """

    employee = models.ForeignKey(
        'Employee',
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='cost_setups',
        help_text='In-house employee (leave blank for remote)',
    )
    remote_employee = models.ForeignKey(
        'RemoteEmployee',
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='cost_setups',
        help_text='Remote employee (leave blank for in-house)',
    )
    effective_from = models.DateField(
        help_text='Date from which this cost setup is effective',
    )

    # ── Cost components ────────────────────────────────────────────────────
    manpower_monthly_fee = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text='Agency / manpower company monthly fee',
    )
    visa_amortisation_monthly = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text='Visa cost spread over visa validity period (monthly)',
    )
    visa_status_change_amortisation = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text='Status change / transfer cost amortised monthly',
    )
    medical_insurance_monthly = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text='Medical / health insurance monthly premium',
    )
    eos_provision_monthly = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text='End-of-service gratuity provision (monthly accrual)',
    )
    leave_salary_provision_monthly = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text='Leave salary provision accrued monthly',
    )
    air_ticket_provision_monthly = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text='Annual return air ticket cost spread monthly',
    )
    recruitment_cost_allocation = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text='Recruitment / placement fee amortised monthly',
    )
    other_cost_monthly = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text='Any other employer cost not covered above',
    )

    notes = models.TextField(
        blank=True,
        help_text='Reason for this revision or any additional context',
    )
    created_by = models.CharField(
        max_length=150,
        help_text='Username of the admin who created this record',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-effective_from', '-created_at']
        verbose_name = 'Employer Cost Setup'
        verbose_name_plural = 'Employer Cost Setups'
        indexes = [
            models.Index(
                fields=['employee', '-effective_from'],
                name='att__ecost_emp_idx',
            ),
            models.Index(
                fields=['remote_employee', '-effective_from'],
                name='att__ecost_remp_idx',
            ),
        ]

    def __str__(self):
        who = (
            self.employee.name if self.employee
            else (self.remote_employee.name if self.remote_employee else '?')
        )
        return f"{who} — cost from {self.effective_from} (total {self.total_monthly_cost:,.2f})"

    @property
    def total_monthly_cost(self):
        """Sum of all nine monthly cost components."""
        return (
            (self.manpower_monthly_fee or 0)
            + (self.visa_amortisation_monthly or 0)
            + (self.visa_status_change_amortisation or 0)
            + (self.medical_insurance_monthly or 0)
            + (self.eos_provision_monthly or 0)
            + (self.leave_salary_provision_monthly or 0)
            + (self.air_ticket_provision_monthly or 0)
            + (self.recruitment_cost_allocation or 0)
            + (self.other_cost_monthly or 0)
        )


# ============================================
# Phase 7 — Employee Document Management
# ============================================

class EmployeeDocument(models.Model):
    """
    Per-employee document register — stores document metadata, file, and expiry.

    Multiple rows per employee (one per document).
    Supports both in-house (Employee) and remote (RemoteEmployee) staff —
    exactly one FK should be set; the other must be null.

    Expiry tracking:
      - days_to_expiry  → int (negative if expired) or None (no expiry set)
      - expiry_status   → 'expired' | 'expiring_soon' | 'valid' | 'no_expiry'
    """

    DOCUMENT_TYPES = [
        ('passport',             'Passport'),
        ('emirates_id',          'Emirates ID'),
        ('uae_visa',             'UAE Visa'),
        ('labour_card',          'Labour Card / Work Permit'),
        ('employment_contract',  'Employment Contract'),
        ('offer_letter',         'Offer Letter'),
        ('medical_insurance',    'Medical Insurance Card'),
        ('educational_cert',     'Educational Certificate'),
        ('nda',                  'NDA / Non-Disclosure Agreement'),
        ('bank_document',        'Bank Document'),
        ('other',                'Other'),
    ]

    employee = models.ForeignKey(
        'Employee',
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='documents',
        help_text='In-house employee (leave blank for remote)',
    )
    remote_employee = models.ForeignKey(
        'RemoteEmployee',
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='documents',
        help_text='Remote employee (leave blank for in-house)',
    )
    document_type = models.CharField(
        max_length=30, choices=DOCUMENT_TYPES,
        help_text='Category of document',
    )
    document_number = models.CharField(
        max_length=100, blank=True,
        help_text='Passport number, EID number, visa number, etc.',
    )
    issue_date = models.DateField(
        null=True, blank=True,
        help_text='Date of issue',
    )
    expiry_date = models.DateField(
        null=True, blank=True,
        help_text='Expiry / valid until date — drives alert colouring',
    )
    issuing_country = models.CharField(
        max_length=100, blank=True,
        help_text='Country that issued the document',
    )
    file = models.FileField(
        upload_to='employee_documents/%Y/%m/',
        null=True, blank=True,
        help_text='Scanned copy or digital version of the document',
    )
    is_verified = models.BooleanField(
        default=False,
        help_text='HR has sighted and verified the original document',
    )
    verified_by = models.CharField(
        max_length=150, blank=True,
        help_text='Username of the HR officer who verified',
    )
    verified_at = models.DateTimeField(
        null=True, blank=True,
        help_text='Timestamp of verification',
    )
    notes = models.TextField(
        blank=True,
        help_text='Any additional notes about this document',
    )
    created_by = models.CharField(
        max_length=150,
        help_text='Username who added this record',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['document_type', '-created_at']
        verbose_name = 'Employee Document'
        verbose_name_plural = 'Employee Documents'
        indexes = [
            models.Index(
                fields=['employee', 'document_type'],
                name='att__edoc_emp_type_idx',
            ),
            models.Index(
                fields=['expiry_date'],
                name='att__edoc_expiry_idx',
            ),
        ]

    def __str__(self):
        who = (
            self.employee.name if self.employee
            else (self.remote_employee.name if self.remote_employee else '?')
        )
        return f"{who} — {self.get_document_type_display()} ({self.document_number or 'no number'})"

    @property
    def days_to_expiry(self):
        """Days until expiry (negative = already expired). None if no expiry date set."""
        if not self.expiry_date:
            return None
        from datetime import date
        return (self.expiry_date - date.today()).days

    @property
    def expiry_status(self):
        """
        Returns one of:
          'expired'       — expiry_date is in the past
          'expiring_soon' — expires within 30 days
          'valid'         — expires in more than 30 days
          'no_expiry'     — no expiry date set
        """
        d = self.days_to_expiry
        if d is None:
            return 'no_expiry'
        if d < 0:
            return 'expired'
        if d <= 30:
            return 'expiring_soon'
        return 'valid'


# ============================================
# Phase 8 — Recoverable Sub-Ledger
# ============================================

class Recoverable(models.Model):
    """
    Tracks amounts the employee owes the company — visa costs, salary advances,
    asset loans, training costs, air ticket recoveries, etc.

    Each row is one recoverable item. The `amount_recovered` field is updated
    incrementally as payroll deductions are processed (Phase 9) or manually
    via the admin. `outstanding_balance` is the computed difference.

    Supports both in-house (Employee) and remote (RemoteEmployee) — exactly one
    FK should be set; the other must be null.
    """

    RECOVERABLE_TYPES = [
        ('visa_cost',   'Visa Cost'),
        ('advance',     'Salary Advance'),
        ('asset',       'Asset / Equipment'),
        ('training',    'Training Cost'),
        ('air_ticket',  'Air Ticket'),
        ('relocation',  'Relocation Cost'),
        ('other',       'Other'),
    ]

    STATUS_CHOICES = [
        ('active',   'Active'),    # outstanding balance > 0, recovery in progress
        ('settled',  'Settled'),   # fully recovered
        ('waived',   'Waived'),    # written off / forgiven
        ('on_hold',  'On Hold'),   # temporarily paused
    ]

    employee = models.ForeignKey(
        'Employee',
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='recoverables',
        help_text='In-house employee (leave blank for remote)',
    )
    remote_employee = models.ForeignKey(
        'RemoteEmployee',
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='recoverables',
        help_text='Remote employee (leave blank for in-house)',
    )
    recoverable_type = models.CharField(
        max_length=30, choices=RECOVERABLE_TYPES,
        help_text='Nature of the amount to be recovered',
    )
    description = models.CharField(
        max_length=255,
        help_text='Brief description of what this recoverable is for',
    )
    total_amount = models.DecimalField(
        max_digits=12, decimal_places=2,
        help_text='Total amount to be recovered from the employee',
    )
    currency = models.CharField(
        max_length=3, default='AED',
        help_text='ISO currency code (e.g. AED, INR, NPR)',
    )
    monthly_recovery = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text='Planned monthly deduction amount',
    )
    recovery_start_year = models.IntegerField(
        help_text='Year in which monthly recovery begins',
    )
    recovery_start_month = models.IntegerField(
        help_text='Month (1–12) in which monthly recovery begins',
    )
    amount_recovered = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text='Cumulative amount recovered to date (updated by payroll or admin)',
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='active', db_index=True,
        help_text="'active' = recovery ongoing; 'settled' = fully paid; "
                  "'waived' = written off; 'on_hold' = paused",
    )
    notes = models.TextField(
        blank=True,
        help_text='Additional context or comments',
    )
    # Waiver fields — populated only when status = 'waived'
    waived_by = models.CharField(
        max_length=150, blank=True,
        help_text='Username who authorised the waiver',
    )
    waived_at = models.DateTimeField(
        null=True, blank=True,
        help_text='When the waiver was granted',
    )
    waived_reason = models.TextField(
        blank=True,
        help_text='Reason the outstanding amount was waived',
    )
    created_by = models.CharField(
        max_length=150,
        help_text='Username who created this record',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Recoverable'
        verbose_name_plural = 'Recoverables'
        indexes = [
            models.Index(
                fields=['employee', 'status'],
                name='att__rec_emp_status_idx',
            ),
            models.Index(
                fields=['remote_employee', 'status'],
                name='att__rec_remp_status_idx',
            ),
        ]

    def __str__(self):
        who = (
            self.employee.name if self.employee
            else (self.remote_employee.name if self.remote_employee else '?')
        )
        return (
            f"{who} — {self.get_recoverable_type_display()} "
            f"{self.currency} {self.total_amount} ({self.status})"
        )

    @property
    def outstanding_balance(self):
        """Amount still to be recovered = total − recovered so far."""
        return (self.total_amount or 0) - (self.amount_recovered or 0)

    @property
    def is_settled(self):
        """True when the outstanding balance is zero or negative."""
        return self.outstanding_balance <= 0


# ============================================
# Remote Employee Models (Call Statistics Based)
# ============================================

class RemoteEmployee(BaseEmployee):
    """Remote employee tracked via phone call statistics."""
    extension_id = models.CharField(max_length=50, db_index=True)

    salary = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    designation = models.CharField(max_length=100, null=True, blank=True)

    WARNING_COUNT_CHOICES = [
        (0, 'Good Standing'),
        (1, 'First Warning'),
        (2, 'Final Warning'),
    ]
    warning_count = models.PositiveSmallIntegerField(
        default=0,
        choices=WARNING_COUNT_CHOICES,
        help_text="Remote Sales target warning stage (Employee Onboarding Policy §4). Resets to 0 once target is met again."
    )
    last_warning_date = models.DateField(
        null=True, blank=True,
        help_text="Date the current warning was issued"
    )

    class Meta:
        unique_together = ('extension_id', 'name')
        verbose_name = 'Remote Employee'
        verbose_name_plural = 'Remote Employees'

    def __str__(self):
        return f"{self.name} ({self.extension_id})"


class RemoteCallRecord(models.Model):
    """Daily call statistics for remote employees."""
    ATTENDANCE_STATUS_CHOICES = [
        ('present', 'Present'),
        ('half_day', 'Half Day'),
        ('absent', 'Absent'),
    ]

    employee = models.ForeignKey(RemoteEmployee, on_delete=models.CASCADE)
    date = models.DateField(db_index=True)

    answered_calls = models.IntegerField(default=0)
    no_answered = models.IntegerField(default=0)
    busy = models.IntegerField(default=0)
    failed = models.IntegerField(default=0)
    voicemail = models.IntegerField(default=0)

    total_ring_duration = models.DurationField(null=True, blank=True)
    total_talk_duration = models.DurationField(null=True, blank=True)

    attendance_status = models.CharField(
        max_length=20, choices=ATTENDANCE_STATUS_CHOICES, default='absent'
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('employee', 'date')
        verbose_name = 'Remote Call Record'
        verbose_name_plural = 'Remote Call Records'
        ordering = ['-date', 'employee__name']
        indexes = [
            models.Index(fields=['employee', 'date']),
        ]

    def __str__(self):
        return f"{self.employee.name} - {self.date} ({self.attendance_status})"

    # Default call-minute thresholds per day type: (half_day_min, present_min)
    DEFAULT_THRESHOLDS = {
        'weekday': (45, 90),    # Mon-Thu
        'friday': (30, 60),
        'saturday': (21, 45),
    }

    def calculate_attendance_status(self, thresholds=None):
        """
        Calculate attendance status based on talk duration and day of week.

        Args:
            thresholds: Optional dict with keys 'weekday', 'friday', 'saturday',
                        each mapping to (half_day_min, present_min) tuples.
                        Falls back to DEFAULT_THRESHOLDS for any missing key.

        Fixed salary employees: any call activity = Present.
        """
        if self.employee.is_fixed_salary:
            total_calls = (self.answered_calls or 0) + (self.no_answered or 0) + (self.busy or 0) + (self.failed or 0)
            return 'present' if total_calls > 0 else 'absent'

        if not self.total_talk_duration:
            return 'absent'

        weekday = self.date.weekday()  # 0=Monday, 6=Sunday
        talk_minutes = self.total_talk_duration.total_seconds() / 60

        if weekday == 6:  # Sunday - Holiday
            return 'present'

        t = thresholds or {}
        if weekday == 5:  # Saturday
            half_min, present_min = t.get('saturday', self.DEFAULT_THRESHOLDS['saturday'])
        elif weekday == 4:  # Friday
            half_min, present_min = t.get('friday', self.DEFAULT_THRESHOLDS['friday'])
        else:  # Monday-Thursday
            half_min, present_min = t.get('weekday', self.DEFAULT_THRESHOLDS['weekday'])

        if talk_minutes >= present_min:
            return 'present'
        elif talk_minutes >= half_min:
            return 'half_day'
        else:
            return 'absent'

    def save(self, *args, **kwargs):
        self.attendance_status = self.calculate_attendance_status()
        super().save(*args, **kwargs)


class RemoteMonthlySummary(models.Model):
    """Monthly attendance summary for remote employees."""
    employee = models.ForeignKey(RemoteEmployee, on_delete=models.CASCADE)
    year = models.IntegerField()
    month = models.IntegerField()

    present_days = models.IntegerField(default=0)
    half_days = models.IntegerField(default=0)
    absent_days = models.IntegerField(default=0)

    total_calls = models.IntegerField(default=0)
    total_talk_time = models.DurationField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('employee', 'year', 'month')
        verbose_name = 'Remote Monthly Summary'
        verbose_name_plural = 'Remote Monthly Summaries'
        ordering = ['-year', '-month', 'employee__name']

    def __str__(self):
        return f"{self.employee.name} - {self.year}/{self.month}"


class EmployeeIDAlias(models.Model):
    """
    Tracks historical person_id values for in-house employees.

    When an employee's person_id changes in the fingerprint machine,
    the old ID is archived here so future uploads can still resolve
    the correct employee record even after the ID changes.

    Lookups against this table are scoped to active employees only —
    if an employee is deactivated, their old IDs are considered released
    and can be assigned to new employees without conflict.
    """
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='id_aliases')
    person_id = models.CharField(max_length=50, db_index=True)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('employee', 'person_id')
        verbose_name = 'Employee ID Alias'
        verbose_name_plural = 'Employee ID Aliases'

    def __str__(self):
        return f"{self.employee.name}: old ID {self.person_id}"


class RemoteEmployeeIDAlias(models.Model):
    """
    Tracks historical extension_id values for remote employees.

    Same purpose as EmployeeIDAlias but for remote employees whose
    phone system extension changes.
    """
    employee = models.ForeignKey(RemoteEmployee, on_delete=models.CASCADE, related_name='id_aliases')
    extension_id = models.CharField(max_length=50, db_index=True)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('employee', 'extension_id')
        verbose_name = 'Remote Employee ID Alias'
        verbose_name_plural = 'Remote Employee ID Aliases'

    def __str__(self):
        return f"{self.employee.name}: old ext {self.extension_id}"


class EarlyLeaveRequest(models.Model):
    """Request for early leave (field visits, customer meetings, etc.)."""
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE, null=True, blank=True
    )
    remote_employee = models.ForeignKey(
        RemoteEmployee, on_delete=models.CASCADE, null=True, blank=True
    )

    request_date = models.DateField(help_text="Date of early leave")
    leaving_time = models.TimeField(help_text="Time when leaving the office")
    return_time = models.TimeField(
        null=True, blank=True, help_text="Estimated time of return"
    )
    destination = models.CharField(
        max_length=255, help_text="Where they are going"
    )
    customer_name = models.CharField(
        max_length=255, help_text="Customer/Client they are meeting"
    )
    reason = models.TextField(blank=True, help_text="Additional details or reason")

    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='pending'
    )
    admin_notes = models.TextField(
        blank=True, help_text="Admin comments on the request"
    )

    approved_first_in = models.TimeField(
        null=True, blank=True,
        help_text="Admin-approved first in time (for merging with biometric data)"
    )
    approved_last_out = models.TimeField(
        null=True, blank=True,
        help_text="Admin-approved last out time (for merging with biometric data)"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Early Leave Request'
        verbose_name_plural = 'Early Leave Requests'

    def __str__(self):
        emp_name = self.employee.name if self.employee else self.remote_employee.name
        return f"{emp_name} - {self.request_date} ({self.status})"

    def clean(self):
        super().clean()
        if self.employee and self.remote_employee:
            raise ValidationError(
                "An early leave request must be linked to either an in-house employee "
                "or a remote employee, not both."
            )
        if not self.employee and not self.remote_employee:
            raise ValidationError(
                "An early leave request must be linked to an employee."
            )

    def get_employee_name(self):
        """Return the employee name regardless of type."""
        return self.employee.name if self.employee else self.remote_employee.name


class LeaveRequest(models.Model):
    """
    Leave request from employees. Supports 4 leave types:
    - Sick Leave (requires document)
    - Medical Leave (requires document)
    - Annual Leave
    - Casual Leave
    """
    LEAVE_TYPE_CHOICES = [
        ('sick', 'Sick Leave'),
        ('medical', 'Medical Leave'),
        ('annual', 'Annual Leave'),
        ('casual', 'Casual Leave'),
    ]

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name='leave_requests'
    )

    leave_type = models.CharField(max_length=20, choices=LEAVE_TYPE_CHOICES)
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField(help_text="Reason for leave request")

    document = models.FileField(
        upload_to='leave_documents/%Y/%m/',
        null=True,
        blank=True,
        help_text="Required for Sick and Medical leave"
    )

    requested_days = models.PositiveIntegerField(
        help_text="Number of days requested"
    )
    approved_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Number of days approved (can be less than requested)"
    )

    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='pending'
    )
    admin_notes = models.TextField(
        blank=True, help_text="Admin comments or rejection reason"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Leave Request'
        verbose_name_plural = 'Leave Requests'
        indexes = [
            models.Index(fields=['employee', 'start_date']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f"{self.employee.name} - {self.get_leave_type_display()} ({self.start_date} to {self.end_date})"

    def clean(self):
        super().clean()
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({
                'end_date': "End date cannot be before start date."
            })

    def save(self, *args, **kwargs):
        if self.start_date and self.end_date:
            delta = (self.end_date - self.start_date).days + 1
            self.requested_days = max(1, delta)
        super().save(*args, **kwargs)

    @property
    def requires_document(self):
        """Check if this leave type requires a document."""
        return self.leave_type in ('sick', 'medical')

    def get_effective_days(self):
        """Return approved days if approved, otherwise requested days."""
        if self.status == 'approved' and self.approved_days is not None:
            return self.approved_days
        return self.requested_days


class AnnualLeave(models.Model):
    """
    Admin-assigned annual leave for employees.
    Tracks paid/unpaid annual leave with optional salary percentage for partial pay.
    Used in payroll to offset absent-day deductions during the leave period.
    """
    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE, null=True, blank=True,
        related_name='annual_leaves'
    )
    remote_employee = models.ForeignKey(
        RemoteEmployee, on_delete=models.CASCADE, null=True, blank=True,
        related_name='annual_leaves'
    )

    start_date = models.DateField()
    end_date = models.DateField()

    actual_rejoining_date = models.DateField(
        null=True, blank=True,
        help_text="The date the employee actually returned to work. Often "
                  "end_date + 1, but not always \u2014 an employee can come back "
                  "late or early, and Sunday entitlement restarts from the day "
                  "they were really back, not the day the leave was scheduled "
                  "to end. Left blank, the Sunday engine infers end_date + 1 "
                  "and flags the figure as inferred.",
    )
    is_paid = models.BooleanField(
        default=True,
        help_text="Whether the employee is paid during this annual leave period"
    )
    salary_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, default=50,
        help_text="Percentage of normal salary paid during leave (0–100). Only "
                  "relevant when is_paid=True. Company rule: annual leave is paid "
                  "at 50% of gross, on the same daily rate as paid leave and paid "
                  "holidays (gross / days in the pay period). Existing rows keep "
                  "whatever percentage they were saved with — this default "
                  "changes new entries only, so no month already paid moves.",
    )

    reason = models.TextField(blank=True, help_text="Reason for the annual leave")
    admin_notes = models.TextField(blank=True, help_text="Admin notes")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-start_date']
        verbose_name = 'Annual Leave'
        verbose_name_plural = 'Annual Leaves'

    def __str__(self):
        name = self.employee.name if self.employee else self.remote_employee.name
        return f"{name} - {self.start_date} to {self.end_date}"

    def clean(self):
        super().clean()
        if self.employee and self.remote_employee:
            raise ValidationError(
                "Annual leave must be linked to either an in-house or remote employee, not both."
            )
        if not self.employee and not self.remote_employee:
            raise ValidationError("Annual leave must be linked to an employee.")
        if self.end_date and self.start_date and self.end_date < self.start_date:
            raise ValidationError({'end_date': "End date cannot be before start date."})

    def get_employee_name(self):
        return self.employee.name if self.employee else self.remote_employee.name

    def get_employee_type(self):
        return 'inhouse' if self.employee else 'remote'

    def get_days_count(self):
        return (self.end_date - self.start_date).days + 1


class UserProfile(models.Model):
    """Extends the built-in Django User with app-specific flags.

    is_it_admin gates access to the custom User Management page — separate
    from is_superuser, which only grants Django Admin access.
    """
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile')
    is_it_admin = models.BooleanField(
        default=False,
        help_text="Grants access to the custom User Management page (in addition to Django Admin)."
    )
    ROLE_NONE = ''
    ROLE_HR_ADMIN = 'hr_admin'
    ROLE_EXEC_DIRECTOR = 'exec_director'
    ROLE_MANAGER = 'manager'
    ROLE_IT = 'it'
    ROLE_CHOICES = [
        (ROLE_NONE, '— none —'),
        (ROLE_HR_ADMIN, 'HR Admin'),
        (ROLE_EXEC_DIRECTOR, 'Executive Director'),
        (ROLE_MANAGER, 'Manager'),
        (ROLE_IT, 'IT'),
    ]
    role = models.CharField(
        max_length=20, choices=ROLE_CHOICES, default=ROLE_NONE, blank=True,
        db_index=True,
        help_text=(
            "Business role. Governs which employee compliance fields this user "
            "may see, separately from which pages they may open. A user with no "
            "role sees no identity numbers, no bank details and no commission "
            "fields, however many sidebar sections they have been granted."
        ),
    )

    sections_restricted = models.BooleanField(
        default=False,
        help_text="When enabled, this user only sees the sidebar pages listed in Allowed Sections, "
                   "regardless of their superuser status. When disabled, they have full access."
    )
    allowed_sections = models.JSONField(
        default=list, blank=True,
        help_text="Sidebar page keys this user may access. Only enforced when Sections Restricted is checked."
    )

    def __str__(self):
        return f"{self.user.username} profile"


class AuditLog(models.Model):
    """
    Phase 13 — Append-only audit trail for financial and role-changing actions.

    Deliberately generic (app_label/model_name/object_id/object_repr as plain
    fields, not a GenericForeignKey) so a deleted source row never breaks the
    audit history and so this model has zero FK coupling to payroll models
    (avoids any cross-app migration-ordering fragility).

    Populated two ways:
      1. Explicit log_audit() calls at known mutation points where the real
         request/actor is available (PayrollRun.advance(), employee profile
         section saves, admin save_model/delete_model overrides).
      2. A best-effort post_save/post_delete signal for models edited only
         from code this app cannot safely instrument (e.g. DeductionEntry,
         edited solely from the payroll views.py monolith) — those rows log
         actor='system' since no request user is available to a signal.
    """
    ACTION_CREATE     = 'create'
    ACTION_UPDATE     = 'update'
    ACTION_DELETE     = 'delete'
    ACTION_TRANSITION = 'transition'
    # Reading a masked identity number is an event in its own right. Without
    # it, nobody can answer "who looked at this Emirates ID, and when" — and
    # masking it on the page would be theatre.
    ACTION_VIEW       = 'view'

    ACTION_CHOICES = [
        (ACTION_CREATE,     'Create'),
        (ACTION_UPDATE,     'Update'),
        (ACTION_DELETE,     'Delete'),
        (ACTION_TRANSITION, 'Status Change'),
        (ACTION_VIEW,       'Sensitive Data Viewed'),
    ]

    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    actor = models.CharField(
        max_length=150, blank=True,
        help_text="Username of the person who made the change, or 'system' when captured via signal.",
    )
    action = models.CharField(max_length=20, choices=ACTION_CHOICES, db_index=True)

    app_label  = models.CharField(max_length=50, db_index=True)
    model_name = models.CharField(max_length=50, db_index=True)
    object_id  = models.CharField(max_length=50, blank=True)
    object_repr = models.CharField(max_length=255, blank=True)

    changes = models.JSONField(
        default=dict, blank=True,
        help_text="{field: [old_value, new_value], ...} — best-effort, empty for pure creates.",
    )
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-timestamp']
        verbose_name = 'Audit Log Entry'
        verbose_name_plural = 'Audit Log Entries'
        indexes = [
            models.Index(fields=['-timestamp'], name='att__audit_ts_idx'),
            models.Index(fields=['app_label', 'model_name', 'object_id'], name='att__audit_obj_idx'),
            models.Index(fields=['actor', '-timestamp'], name='att__audit_actor_idx'),
        ]

    def __str__(self):
        return f'{self.get_action_display()} {self.model_name} #{self.object_id} by {self.actor or "system"} @ {self.timestamp:%Y-%m-%d %H:%M}'


class Company(models.Model):
    """A legal entity. Taamul, NAAS, and whatever comes next.

    ADDED BY THE USER, NOT SEEDED BY A DEVELOPER. bhoopal's instruction on
    17 Aug 2026 was that entities must be addable as and when required, so this
    ships with no hard-coded list: one default row is created from the existing
    data and everything after that is entered through the UI.

    Deliberately LEAN. The specification (§82) lists work week, leave rules,
    payroll rules, approval rules, WPS configuration, gratuity rules, holiday
    calendar and salary components as per-company settings. None of them are
    fields here, because each belongs on the table that already owns that
    concept — a holiday calendar is a property of Holiday, not of Company.
    Putting them here would create a second place for the same rule to live and
    a second chance for the two to disagree. They arrive as `company` foreign
    keys on those tables, one phase at a time.
    """
    code = models.CharField(
        max_length=12, unique=True,
        help_text="Short handle, e.g. TAM or NAAS. Used in reports and exports. "
                  "NOT used to build employee numbers — those stay in the TCR "
                  "format, entered by hand (bhoopal, 17 Aug 2026).",
    )
    name = models.CharField(max_length=120, help_text="Trading name as people say it.")
    legal_name = models.CharField(
        max_length=200, blank=True, default='',
        help_text="Full registered name, for contracts and letters. Blank is "
                  "allowed — an unfilled field is better than a guessed one on "
                  "a document somebody signs.",
    )
    trade_licence_number = models.CharField(max_length=60, blank=True, default='')
    establishment_number = models.CharField(
        max_length=60, blank=True, default='',
        help_text="MOHRE establishment number, where the entity has one.",
    )
    default_labour_jurisdiction = models.CharField(
        max_length=20, blank=True, default='',
        help_text="Suggested jurisdiction for new employees of this entity. A "
                  "SUGGESTION, not an override: jurisdiction is decided per "
                  "employee, because one entity can hold MOHRE staff, own-visa "
                  "staff and offshore staff at the same time — as this one does.",
    )
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'Companies'

    def __str__(self):
        return f'{self.name} ({self.code})'


class EmployeeAssignment(models.Model):
    """Where a person sat, and when. The §1 principle, made concrete.

    Today `department`, `team`, `location`, `designation` and
    `reporting_manager` are single overwritable fields on the employee row.
    Change someone's manager and the previous manager stops having existed —
    which makes every historical approval, every past appraisal and every "who
    signed this off" question unanswerable. This table is the record that
    survives the change.

    SHAPES ARE COPIED, NOT IMPROVED
    -------------------------------
    `department`, `team`, `location` and `designation` are CharFields here
    because that is exactly what they are on the employee row today, even
    though Department, Team, Location and DesignationMaster tables exist beside
    them. Normalising strings onto those master tables is real work with real
    ambiguity ("Admin" vs "Administration") and it belongs in its own pass with
    its own reconciliation. Copying the shapes verbatim is what makes the
    backfill provably lossless: every value that goes in comes back out
    identical, and that can be asserted row by row.

    OVERLAP
    -------
    One assignment per employee may be current at a time, and periods must not
    overlap. That is enforced in `clean()` and in
    `services_assignments.open_assignment()`, NOT by a partial unique index —
    MySQL does not support conditional constraints, so a
    `UniqueConstraint(condition=...)` would be silently useless on the very
    database this runs on. What IS enforced at database level is the part MySQL
    can do: no two assignments for the same person starting on the same day.
    """

    CHANGE_JOINING = 'joining'
    CHANGE_PROMOTION = 'promotion'
    CHANGE_TRANSFER = 'transfer'
    CHANGE_MANAGER = 'manager_change'
    CHANGE_REGRADE = 'regrade'
    CHANGE_CORRECTION = 'correction'
    CHANGE_REHIRE = 'rehire'
    CHANGE_OTHER = 'other'
    CHANGE_TYPE_CHOICES = [
        (CHANGE_JOINING, 'Joining'),
        (CHANGE_PROMOTION, 'Promotion'),
        (CHANGE_TRANSFER, 'Transfer'),
        (CHANGE_MANAGER, 'Manager change'),
        (CHANGE_REGRADE, 'Grade change'),
        (CHANGE_CORRECTION, 'Correction'),
        (CHANGE_REHIRE, 'Rehire'),
        (CHANGE_OTHER, 'Other'),
    ]

    employee = models.ForeignKey(
        'attendance.Employee', on_delete=models.CASCADE,
        null=True, blank=True, related_name='assignments')
    remote_employee = models.ForeignKey(
        'attendance.RemoteEmployee', on_delete=models.CASCADE,
        null=True, blank=True, related_name='assignments')
    company = models.ForeignKey(
        'attendance.Company', on_delete=models.PROTECT,
        null=True, blank=True, related_name='assignments',
        help_text="The entity as at this assignment. Held here as well as on "
                  "the employee because moving between entities IS a transfer, "
                  "and the old row has to keep saying where they used to be.")

    effective_from = models.DateField(db_index=True)
    effective_to = models.DateField(
        null=True, blank=True, db_index=True,
        help_text="Blank means open-ended — this is the arrangement in force. "
                  "Closed by the next assignment, never by hand.")
    is_current = models.BooleanField(
        default=True, db_index=True,
        help_text="Denormalised for querying. `effective_to IS NULL` is the "
                  "truth; this is the index that makes 'who is where today' "
                  "cheap. services_assignments keeps the two in step.")

    department = models.CharField(max_length=100, blank=True, default='')
    team = models.CharField(max_length=100, blank=True, default='')
    location = models.CharField(max_length=100, blank=True, default='')
    designation = models.CharField(max_length=100, blank=True, default='')
    grade = models.CharField(
        max_length=50, blank=True, default='',
        help_text="New — no grade exists on the employee row today, so this is "
                  "empty on every backfilled row rather than invented.")
    job_level = models.CharField(max_length=50, blank=True, default='')
    cost_centre = models.CharField(max_length=50, blank=True, default='')

    reporting_manager = models.ForeignKey(
        'attendance.Employee', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='managed_assignments')
    functional_manager = models.ForeignKey(
        'attendance.Employee', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='functionally_managed_assignments')

    change_type = models.CharField(
        max_length=20, choices=CHANGE_TYPE_CHOICES, default=CHANGE_OTHER, db_index=True)
    reason = models.TextField(
        blank=True, default='',
        help_text="Why this changed. Empty on backfilled rows, which is honest: "
                  "nobody recorded a reason at the time and inventing one would "
                  "put words in somebody's mouth.")

    approved_by = models.CharField(max_length=150, blank=True, default='')
    approved_at = models.DateTimeField(null=True, blank=True)
    created_by = models.CharField(max_length=150, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-effective_from', '-id']
        verbose_name = 'Employee assignment'
        indexes = [
            models.Index(fields=['employee', 'is_current']),
            models.Index(fields=['remote_employee', 'is_current']),
            models.Index(fields=['employee', 'effective_from']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['employee', 'effective_from'],
                name='uniq_assignment_inhouse_start'),
            models.UniqueConstraint(
                fields=['remote_employee', 'effective_from'],
                name='uniq_assignment_remote_start'),
        ]

    def __str__(self):
        who = self.employee or self.remote_employee
        end = self.effective_to.isoformat() if self.effective_to else 'current'
        return f'{who} — {self.designation or self.department or "assignment"} ({self.effective_from} to {end})'

    @property
    def person(self):
        return self.employee or self.remote_employee

    def clean(self):
        super().clean()
        if self.employee and self.remote_employee:
            raise ValidationError(
                'An assignment belongs to either an in-house or a remote employee, not both.')
        if not self.employee and not self.remote_employee:
            raise ValidationError('An assignment must belong to an employee.')
        if self.effective_to and self.effective_from and self.effective_to < self.effective_from:
            raise ValidationError({'effective_to': 'Cannot end before it starts.'})

        # Overlap. Enforced here because MySQL cannot express it as a constraint.
        if self.effective_from:
            qs = EmployeeAssignment.objects.filter(
                employee=self.employee, remote_employee=self.remote_employee)
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            end = self.effective_to
            for other in qs:
                o_end = other.effective_to
                starts_before_other_ends = (o_end is None or self.effective_from <= o_end)
                other_starts_before_this_ends = (end is None or other.effective_from <= end)
                if starts_before_other_ends and other_starts_before_this_ends:
                    raise ValidationError(
                        'Overlaps an existing assignment (%s to %s). Close that one first.'
                        % (other.effective_from, o_end or 'current'))


class EmployeeTimelineEvent(models.Model):
    """One line in the story of an employee. §46 calls this mandatory.

    Joined · confirmed · salary revised · promoted · manager changed · leave
    taken · warning issued · visa renewed · resigned. One chronology, filterable,
    that answers "what happened to this person" without opening six screens.

    DEDUPE, AND WHY IT IS A STRING
    ------------------------------
    Events are written by backfills and by live code, and both run more than
    once. Without a key, re-running a backfill doubles everyone's history —
    which is worse than no timeline, because it looks authoritative.

    The key is a single unique CharField rather than a composite unique index
    across (employee, remote_employee, type, source, date). MySQL treats NULLs
    in a unique index as distinct, and exactly one of those two FKs is always
    NULL, so the composite version would permit duplicates on the very database
    this runs on. 190 characters because that is the utf8mb4 index limit.

    SOURCE IS A STRING PAIR, NOT A GENERIC FOREIGN KEY
    --------------------------------------------------
    Same choice AuditLog already made in this codebase: `source_model` and
    `source_id` as plain fields, so the attendance app never takes a hard
    dependency on payroll to render a payroll event.
    """

    CATEGORY_CHOICES = [
        ('employment', 'Employment'),
        ('salary', 'Salary'),
        ('leave', 'Leave'),
        ('attendance', 'Attendance'),
        ('promotion', 'Promotion'),
        ('warning', 'Warning'),
        ('document', 'Document'),
        ('payroll', 'Payroll'),
        ('performance', 'Performance'),
        ('compliance', 'Compliance'),
        ('other', 'Other'),
    ]

    employee = models.ForeignKey(
        'attendance.Employee', on_delete=models.CASCADE,
        null=True, blank=True, related_name='timeline_events')
    remote_employee = models.ForeignKey(
        'attendance.RemoteEmployee', on_delete=models.CASCADE,
        null=True, blank=True, related_name='timeline_events')

    event_date = models.DateField(db_index=True)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES,
                                default='other', db_index=True)
    event_type = models.CharField(max_length=50, db_index=True)
    title = models.CharField(max_length=200)
    detail = models.TextField(blank=True, default='')

    source_model = models.CharField(max_length=60, blank=True, default='')
    source_id = models.CharField(max_length=40, blank=True, default='')

    dedupe_key = models.CharField(max_length=190, unique=True)
    created_by = models.CharField(max_length=150, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-event_date', '-id']
        indexes = [
            models.Index(fields=['employee', 'event_date']),
            models.Index(fields=['remote_employee', 'event_date']),
            models.Index(fields=['category', 'event_date']),
        ]

    def __str__(self):
        return f'{self.event_date} — {self.title}'

    @property
    def person(self):
        return self.employee or self.remote_employee


class ApprovalChain(models.Model):
    """Who has to say yes to a given kind of transaction, in what order.

    Configurable per company, with a fallback: a chain whose `company` is blank
    applies to every entity that has no chain of its own. That is what makes
    adding a second entity cheap — it inherits until somebody says otherwise.

    §58 also wants chains varying by department, amount and grade. Not built:
    those conditions have no callers yet, and a configuration surface nobody
    fills in is a place for stale rules to hide.
    """
    request_type = models.CharField(max_length=40, db_index=True)
    company = models.ForeignKey(
        'attendance.Company', on_delete=models.CASCADE,
        null=True, blank=True, related_name='approval_chains',
        help_text="Blank means this chain is the fallback for every entity.")
    description = models.CharField(max_length=200, blank=True, default='')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['request_type', 'company__code']
        constraints = [
            models.UniqueConstraint(fields=['request_type', 'company'],
                                    name='uniq_chain_per_type_company'),
        ]

    def __str__(self):
        return f'{self.request_type} — {self.company.code if self.company else "all entities"}'


class ApprovalChainStep(models.Model):
    """One rung of a chain: a role that must approve, and where it sits."""
    chain = models.ForeignKey(ApprovalChain, on_delete=models.CASCADE, related_name='steps')
    sequence = models.PositiveIntegerField(help_text='1 = first approver.')
    role_required = models.CharField(
        max_length=30,
        help_text="Matches UserProfile.role. Kept as a string rather than a "
                  "choices list so a role added later does not need a migration "
                  "here as well.")
    label = models.CharField(max_length=100, blank=True, default='')

    class Meta:
        ordering = ['sequence']
        constraints = [
            models.UniqueConstraint(fields=['chain', 'sequence'], name='uniq_step_per_chain'),
        ]

    def __str__(self):
        return f'{self.chain.request_type} #{self.sequence} {self.role_required}'


class ApprovalRequest(models.Model):
    """A transaction waiting for permission, with the values as submitted.

    §89: the payload is a SNAPSHOT. If the employee master changes while this
    sits pending, the approver still sees exactly what was submitted. Anything
    else means people approve one thing and a different thing takes effect.
    """
    STATUS_PENDING = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_CANCELLED = 'cancelled'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_REJECTED, 'Rejected'),
        (STATUS_CANCELLED, 'Cancelled'),
    ]

    request_type = models.CharField(max_length=40, db_index=True)
    employee = models.ForeignKey(
        'attendance.Employee', on_delete=models.CASCADE,
        null=True, blank=True, related_name='approval_requests')
    remote_employee = models.ForeignKey(
        'attendance.RemoteEmployee', on_delete=models.CASCADE,
        null=True, blank=True, related_name='approval_requests')
    company = models.ForeignKey(
        'attendance.Company', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='approval_requests')

    payload = models.JSONField(
        default=dict,
        help_text="The submitted values, frozen. Never recomputed from the "
                  "employee record — that is the whole point (§89).")
    summary = models.CharField(
        max_length=300, blank=True, default='',
        help_text="Human-readable one-liner, also frozen at submit time.")
    effective_date = models.DateField(null=True, blank=True)
    reason = models.TextField(blank=True, default='')

    status = models.CharField(max_length=12, choices=STATUS_CHOICES,
                              default=STATUS_PENDING, db_index=True)
    submitted_by = models.CharField(max_length=150, blank=True, default='')
    submitted_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    applied_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When the approved change actually took effect. Separate from "
                  "decided_at because approval and application can fail apart.")
    apply_error = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-submitted_at']
        indexes = [models.Index(fields=['status', 'request_type'])]

    def __str__(self):
        return f'{self.request_type} for {self.person} ({self.status})'

    @property
    def person(self):
        return self.employee or self.remote_employee

    @property
    def pending_step(self):
        return self.steps.filter(decision=ApprovalStep.DECISION_PENDING).order_by('sequence').first()


class ApprovalStep(models.Model):
    """One approver's answer on one request."""
    DECISION_PENDING = 'pending'
    DECISION_APPROVED = 'approved'
    DECISION_REJECTED = 'rejected'
    DECISION_CHOICES = [
        (DECISION_PENDING, 'Pending'),
        (DECISION_APPROVED, 'Approved'),
        (DECISION_REJECTED, 'Rejected'),
    ]

    request = models.ForeignKey(ApprovalRequest, on_delete=models.CASCADE, related_name='steps')
    sequence = models.PositiveIntegerField()
    role_required = models.CharField(max_length=30)
    label = models.CharField(max_length=100, blank=True, default='')
    decision = models.CharField(max_length=12, choices=DECISION_CHOICES,
                                default=DECISION_PENDING, db_index=True)
    decided_by = models.CharField(max_length=150, blank=True, default='')
    decided_at = models.DateTimeField(null=True, blank=True)
    comments = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['sequence']
        constraints = [
            models.UniqueConstraint(fields=['request', 'sequence'],
                                    name='uniq_step_per_request'),
        ]

    def __str__(self):
        return f'#{self.sequence} {self.role_required} — {self.decision}'


class PersonScopedModel(models.Model):
    """Abstract base for the dual-employee pattern.

    Seven Phase 5 tables need the same two nullable foreign keys and the same
    guard: exactly one of them, never both, never neither. Repeating that seven
    times is seven chances to leave the guard off one of them — which is how a
    record ends up belonging to nobody and disappearing from every report that
    joins through an employee.
    """
    employee = models.ForeignKey(
        'attendance.Employee', on_delete=models.CASCADE,
        null=True, blank=True, related_name='%(class)ss')
    remote_employee = models.ForeignKey(
        'attendance.RemoteEmployee', on_delete=models.CASCADE,
        null=True, blank=True, related_name='%(class)ss')

    class Meta:
        abstract = True

    @property
    def person(self):
        return self.employee or self.remote_employee

    def clean(self):
        super().clean()
        if self.employee and self.remote_employee:
            raise ValidationError(
                'This record belongs to either an in-house or a remote employee, not both.')
        if not self.employee and not self.remote_employee:
            raise ValidationError('This record must belong to an employee.')


class EmployeeVisa(PersonScopedModel):
    """UAE residency visas, kept as a history rather than overwritten (§11).

    Today a renewal replaces the previous visa and the old permit number stops
    having existed. That matters: a cancelled visa's file number is what a
    government query is made against months later.

    WHERE EXPIRY LIVES, AND WHY IT IS HERE
    --------------------------------------
    An expiry date is intrinsic to the visa, so it belongs on the visa. But
    `services_compliance.watchlist()` reads expiry from `EmployeeDocument`, and
    two tables holding the same date is how the dashboard ends up disagreeing
    with the record. `document` links this row to the scan that the watchlist
    already tracks so the pair can be reconciled — and until the watchlist is
    pointed at this table, THE DOCUMENT REMAINS THE ONE THE ALERTS COME FROM.
    That is a deliberate, temporary duplication, written down rather than
    discovered later.
    """
    VISA_STATUS_CHOICES = [
        ('active', 'Active'),
        ('expired', 'Expired'),
        ('cancelled', 'Cancelled'),
        ('under_process', 'Under process'),
    ]
    SPONSOR_TYPE_CHOICES = [
        ('company', 'Company'),
        ('spouse', 'Spouse'),
        ('parent', 'Parent'),
        ('self', 'Self / investor'),
        ('free_zone', 'Free zone authority'),
        ('other', 'Other'),
    ]

    uid_number = models.CharField(max_length=40, blank=True, default='',
                                  help_text='Unified ID — stays with the person across visas.')
    visa_file_number = models.CharField(max_length=60, blank=True, default='')
    residence_permit_number = models.CharField(max_length=60, blank=True, default='')
    visa_type = models.CharField(max_length=30, blank=True, default='')
    sponsor = models.CharField(max_length=150, blank=True, default='')
    sponsor_type = models.CharField(max_length=20, choices=SPONSOR_TYPE_CHOICES,
                                    blank=True, default='')
    place_of_issue = models.CharField(max_length=100, blank=True, default='')
    issue_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True, db_index=True)
    status = models.CharField(max_length=20, choices=VISA_STATUS_CHOICES,
                              default='active', db_index=True)
    is_current = models.BooleanField(default=True, db_index=True)
    inside_country = models.BooleanField(
        null=True, blank=True,
        help_text='Issued in-country or out. Null means not recorded — which is '
                  'different from "outside".')
    cancellation_date = models.DateField(null=True, blank=True)
    cancellation_reference = models.CharField(max_length=80, blank=True, default='')
    document = models.ForeignKey(
        'attendance.EmployeeDocument', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='visas', help_text='The scan the compliance watchlist tracks.')
    notes = models.TextField(blank=True, default='')
    created_by = models.CharField(max_length=150, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-issue_date', '-id']
        indexes = [models.Index(fields=['employee', 'is_current']),
                   models.Index(fields=['expiry_date'])]

    def __str__(self):
        return f'{self.person} — visa {self.residence_permit_number or self.visa_file_number or "?"}'


class EmployeeDependent(PersonScopedModel):
    """Spouse and children — sponsorship, documents and cover (§15)."""
    RELATIONSHIP_CHOICES = [
        ('spouse', 'Spouse'), ('son', 'Son'), ('daughter', 'Daughter'),
        ('father', 'Father'), ('mother', 'Mother'), ('other', 'Other'),
    ]
    name = models.CharField(max_length=150)
    relationship = models.CharField(max_length=20, choices=RELATIONSHIP_CHOICES)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=10, blank=True, default='')
    nationality = models.CharField(max_length=80, blank=True, default='')
    passport_number = models.CharField(max_length=60, blank=True, default='')
    passport_expiry = models.DateField(null=True, blank=True)
    emirates_id = models.CharField(max_length=40, blank=True, default='')
    emirates_id_expiry = models.DateField(null=True, blank=True)
    visa_expiry = models.DateField(null=True, blank=True)
    sponsored_by_company = models.BooleanField(
        default=False,
        help_text='Whether the company sponsors this dependent. Drives cost and '
                  'renewal responsibility, so it is not the same as "has a visa".')
    insurance_covered = models.BooleanField(default=False)
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['relationship', 'name']

    def __str__(self):
        return f'{self.name} ({self.get_relationship_display()})'


class EmployeeInsurance(PersonScopedModel):
    """Health insurance, with the cost split (§14)."""
    provider = models.CharField(max_length=120)
    policy_number = models.CharField(max_length=80, blank=True, default='')
    member_number = models.CharField(max_length=80, blank=True, default='')
    category = models.CharField(max_length=60, blank=True, default='')
    network = models.CharField(max_length=60, blank=True, default='')
    coverage_start = models.DateField(null=True, blank=True)
    coverage_end = models.DateField(null=True, blank=True, db_index=True)
    covers_employee = models.BooleanField(default=True)
    covers_dependents = models.BooleanField(default=False)
    total_cost = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    employer_contribution = models.DecimalField(max_digits=10, decimal_places=2,
                                                null=True, blank=True)
    employee_contribution = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text='Any employee share. NOT derived from total minus employer — a '
                  'derived figure would silently absorb a data-entry error into '
                  'a payroll deduction.')
    currency = models.CharField(max_length=3, default='AED')
    is_current = models.BooleanField(default=True, db_index=True)
    document = models.ForeignKey(
        'attendance.EmployeeDocument', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='insurance_policies')
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-coverage_start', '-id']

    def __str__(self):
        return f'{self.provider} — {self.policy_number or "no policy no."}'


class EmployeeMedicalFitness(PersonScopedModel):
    """Medical fitness tests, which recur — hence a table, not fields (§13)."""
    RESULT_CHOICES = [('fit', 'Fit'), ('unfit', 'Unfit'),
                      ('pending', 'Pending'), ('referred', 'Referred')]
    application_number = models.CharField(max_length=80, blank=True, default='')
    test_date = models.DateField(null=True, blank=True)
    centre = models.CharField(max_length=150, blank=True, default='')
    result = models.CharField(max_length=20, choices=RESULT_CHOICES,
                              blank=True, default='')
    certificate_number = models.CharField(max_length=80, blank=True, default='')
    expiry_date = models.DateField(null=True, blank=True, db_index=True)
    document = models.ForeignKey(
        'attendance.EmployeeDocument', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='medical_tests')
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-test_date', '-id']
        verbose_name_plural = 'Employee medical fitness records'

    def __str__(self):
        return f'{self.person} — medical {self.test_date or "undated"}'


class EmployeeEducation(PersonScopedModel):
    """Academic qualifications, with attestation state (§16).

    Attestation is three booleans rather than one status, because MOFA and the
    UAE embassy are separate steps that fail separately, and "attested" without
    saying by whom is not an answer anyone can act on.
    """
    qualification = models.CharField(max_length=120)
    degree = models.CharField(max_length=120, blank=True, default='')
    specialisation = models.CharField(max_length=120, blank=True, default='')
    institution = models.CharField(max_length=200, blank=True, default='')
    country = models.CharField(max_length=80, blank=True, default='')
    start_date = models.DateField(null=True, blank=True)
    completion_date = models.DateField(null=True, blank=True)
    grade = models.CharField(max_length=40, blank=True, default='')
    certificate_number = models.CharField(max_length=80, blank=True, default='')
    attested = models.BooleanField(default=False)
    mofa_attested = models.BooleanField(default=False)
    uae_embassy_attested = models.BooleanField(default=False)
    document = models.ForeignKey(
        'attendance.EmployeeDocument', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='education_records')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-completion_date', '-id']

    def __str__(self):
        return f'{self.qualification} — {self.institution or "?"}'


class EmployeeQualification(PersonScopedModel):
    """Professional licences and memberships — CA, ACCA, CPA, CFA, medical (§17).

    Separate from EmployeeEducation because these EXPIRE and carry CPD
    obligations. Folding them into one table would leave every academic degree
    with a null expiry and every licence with a null grade, and would put a
    lapsing practising licence in a table nothing checks for renewal.
    """
    title = models.CharField(max_length=120)
    issuing_authority = models.CharField(max_length=150, blank=True, default='')
    membership_number = models.CharField(max_length=80, blank=True, default='')
    issue_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True, db_index=True)
    cpd_required = models.BooleanField(default=False)
    cpd_hours_required = models.PositiveIntegerField(null=True, blank=True)
    is_current = models.BooleanField(default=True, db_index=True)
    document = models.ForeignKey(
        'attendance.EmployeeDocument', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='qualifications')
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-issue_date', '-id']

    def __str__(self):
        return f'{self.title} — {self.membership_number or "no member no."}'


class EmployeePreviousEmployment(PersonScopedModel):
    """Employment before this one, and whether the reference was actually taken (§18)."""
    employer = models.CharField(max_length=200)
    country = models.CharField(max_length=80, blank=True, default='')
    designation = models.CharField(max_length=120, blank=True, default='')
    from_date = models.DateField(null=True, blank=True)
    to_date = models.DateField(null=True, blank=True)
    last_salary = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    salary_currency = models.CharField(max_length=3, blank=True, default='')
    reason_for_leaving = models.CharField(max_length=200, blank=True, default='')
    reference_name = models.CharField(max_length=150, blank=True, default='')
    reference_contact = models.CharField(max_length=150, blank=True, default='')
    reference_checked = models.BooleanField(
        default=False,
        help_text='Whether the reference was TAKEN, not whether a contact was '
                  'recorded. An unchecked reference beside a filled-in phone '
                  'number is exactly the gap an audit asks about.')
    reference_checked_by = models.CharField(max_length=150, blank=True, default='')
    reference_checked_at = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-to_date', '-id']
        verbose_name_plural = 'Employee previous employment'

    def __str__(self):
        return f'{self.employer} — {self.designation or "?"}'


class SalaryCycleHistory(PersonScopedModel):
    """Per-employee pay-cycle override, effective-dated by an exact date.

    Most employees never need a row here — they simply follow
    `SalaryCycleDefault`. This table exists for the exception: one employee
    who needs a different cycle than everyone else, from a given date
    onward. See `attendance.services_salary_cycle` for how this and
    `SalaryCycleDefault` are merged into one timeline and CLIPPED either
    side of each `effective_date` so a change never re-pays or skips a day:
    the outgoing cycle's last period ends the day before `effective_date`,
    the incoming cycle's first period starts exactly on it. A period with no
    rows in either table resolves exactly as it did before this feature
    existed (the legacy `salary_cycle_start_day` field, unclipped).
    """
    cycle_start_day = models.PositiveSmallIntegerField(
        help_text="1 = calendar month (1st to last day); 2-28 = day of the "
                   "previous month the cycle starts on"
    )
    effective_date = models.DateField(
        help_text="Exact date this override takes effect (inclusive)"
    )
    note = models.CharField(max_length=255, blank=True)
    created_by = models.CharField(
        max_length=150, blank=True,
        help_text="Username of the admin who created this entry",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-effective_date']
        verbose_name = 'Salary Cycle History'
        verbose_name_plural = 'Salary Cycle Histories'
        constraints = [
            models.UniqueConstraint(
                fields=['employee', 'effective_date'],
                condition=models.Q(employee__isnull=False),
                name='uniq_salary_cycle_emp_date',
            ),
            models.UniqueConstraint(
                fields=['remote_employee', 'effective_date'],
                condition=models.Q(remote_employee__isnull=False),
                name='uniq_salary_cycle_remote_date',
            ),
        ]

    def __str__(self):
        return f"{self.person.name}: day {self.cycle_start_day} (from {self.effective_date})"

    def clean(self):
        super().clean()
        if self.cycle_start_day is not None and not (1 <= self.cycle_start_day <= 28):
            raise ValidationError({'cycle_start_day': 'Cycle start day must be between 1 and 28.'})


class LeaveType(models.Model):
    """Configurable leave types (§28).

    LANDED, NOT WIRED. `LeaveRequest.leave_type` keeps its hard-coded choices
    for now: repointing a field that every leave screen, the payroll engine and
    two reports already read is a migration of live data, not a model addition,
    and it belongs in its own pass. This table is what that pass will read.
    """
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=80)
    is_paid = models.BooleanField(default=True)
    consumes_annual_entitlement = models.BooleanField(
        default=False,
        help_text='Whether taking this draws down the annual leave balance. '
                  'Sick leave does not; annual leave does. Getting this wrong '
                  'is how a balance quietly runs out.')
    requires_document = models.BooleanField(default=False)
    max_days_per_year = models.PositiveIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.name} ({self.code})'


class LeavePolicy(models.Model):
    """A named entitlement rule set — §29 forbids hard-coding these.

    Scoped by jurisdiction, company and employee category so MOHRE staff and
    offshore staff can be governed differently, which is the whole reason the
    jurisdiction field exists. A policy with everything blank is the fallback.
    """
    name = models.CharField(max_length=120)
    leave_type_code = models.CharField(
        max_length=30, default='annual',
        help_text="Which leave this governs. A string, not an FK to LeaveType, "
                  "so a policy can be written before the type table is wired in.")
    labour_jurisdiction = models.CharField(
        max_length=20, blank=True, default='',
        help_text='Blank means it applies regardless of jurisdiction.')
    company = models.ForeignKey('attendance.Company', on_delete=models.CASCADE,
                                null=True, blank=True, related_name='leave_policies')
    employee_category = models.CharField(max_length=40, blank=True, default='')
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['leave_type_code', 'name']
        verbose_name_plural = 'Leave policies'

    def __str__(self):
        bits = [self.labour_jurisdiction or 'any jurisdiction',
                self.company.code if self.company else 'all entities']
        return f'{self.name} ({", ".join(bits)})'


class LeavePolicyVersion(models.Model):
    """The numbers, effective-dated (§29).

    Statutory rules change. When they do, a new VERSION is added with a start
    date — the old one is not edited. That is what lets a settlement computed in
    2025 still be reproducible in 2027 under the rule that actually applied.
    """
    policy = models.ForeignKey(LeavePolicy, on_delete=models.CASCADE, related_name='versions')
    effective_from = models.DateField(db_index=True)
    effective_to = models.DateField(null=True, blank=True)

    min_months_for_entitlement = models.DecimalField(
        max_digits=5, decimal_places=2, default=6,
        help_text='Below this, entitlement is zero — a rule, not a rounding.')
    short_service_days_per_month = models.DecimalField(
        max_digits=5, decimal_places=2, default=2,
        help_text='Days earned per month between the minimum and one year.')
    full_days_per_year = models.DecimalField(max_digits=5, decimal_places=2, default=30)

    accrual_basis = models.CharField(
        max_length=20, default='monthly',
        help_text="'monthly' accrues pro-rata; 'anniversary' credits only on "
                  "completed years. bhoopal chose monthly on 17 Aug 2026 (D8).")
    pay_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, default=50,
        help_text='Percentage of the wage paid while on this leave. 50 per D7.')
    divisor_basis = models.CharField(
        max_length=20, default='period_days',
        help_text="'period_days' divides by days in the pay period, matching the "
                  "payroll engine; 'fixed_30' uses the UAE leave-salary "
                  "convention. They differ by about 3% in a 31-day month, so "
                  "this is a real choice and not a formatting preference.")
    max_carry_forward_days = models.DecimalField(max_digits=5, decimal_places=2,
                                                 null=True, blank=True)
    carry_forward_expires_after_months = models.PositiveIntegerField(null=True, blank=True)
    encashment_allowed = models.BooleanField(default=True)
    encashment_basis = models.CharField(
        max_length=20, default='gross',
        help_text="'gross' or 'basic'. D7 chose gross at the pay percentage; "
                  "Article 29 sets basic-at-100% as the floor for termination.")

    source_reference = models.CharField(
        max_length=200, blank=True, default='',
        help_text='Where this rule comes from — e.g. "Federal Decree-Law 33/2021 '
                  'Art. 29" or "Board minute 12 Aug 2026". An unsourced statutory '
                  'number is impossible to defend later.')
    approved_by = models.CharField(max_length=150, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-effective_from']
        constraints = [
            models.UniqueConstraint(fields=['policy', 'effective_from'],
                                    name='uniq_policy_version_start'),
        ]

    def __str__(self):
        return f'{self.policy.name} from {self.effective_from}'


class LeaveLedgerEntry(PersonScopedModel):
    """Every movement in a leave balance (§30).

    A single `balance = 18` field cannot answer "why 18", cannot be audited, and
    cannot be corrected without destroying what it was before. This is the
    ledger: opening balance, accruals, days taken, adjustments, carry-forward,
    encashment, expiry, reversals — each with a date, a reason and a source.

    `balance_after` is stored rather than recomputed on read. A running balance
    that is derived every time changes retrospectively whenever an old row is
    touched, so a payslip printed last month would no longer reproduce. Stored,
    it is a statement of what the balance WAS at that point.
    """
    KIND_OPENING = 'opening'
    KIND_ACCRUAL = 'accrual'
    KIND_TAKEN = 'taken'
    KIND_ADJUSTMENT = 'adjustment'
    KIND_CARRY_FORWARD = 'carry_forward'
    KIND_ENCASHMENT = 'encashment'
    KIND_EXPIRY = 'expiry'
    KIND_REVERSAL = 'reversal'
    KIND_CHOICES = [
        (KIND_OPENING, 'Opening balance'),
        (KIND_ACCRUAL, 'Accrual'),
        (KIND_TAKEN, 'Leave taken'),
        (KIND_ADJUSTMENT, 'Manual adjustment'),
        (KIND_CARRY_FORWARD, 'Carry forward'),
        (KIND_ENCASHMENT, 'Encashment'),
        (KIND_EXPIRY, 'Expiry'),
        (KIND_REVERSAL, 'Reversal'),
    ]

    leave_type_code = models.CharField(max_length=30, default='annual', db_index=True)
    entry_date = models.DateField(db_index=True)
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, db_index=True)
    days = models.DecimalField(
        max_digits=7, decimal_places=2,
        help_text='POSITIVE credits the balance, NEGATIVE consumes it. One '
                  'signed column rather than debit/credit pairs, because two '
                  'columns invite a row with both filled in.')
    balance_after = models.DecimalField(max_digits=8, decimal_places=2)

    description = models.CharField(max_length=200, blank=True, default='')
    reason = models.TextField(
        blank=True, default='',
        help_text='Required for manual adjustments — enforced in the service, '
                  'because an unexplained balance change is the thing an audit '
                  'goes looking for first.')
    source_model = models.CharField(max_length=60, blank=True, default='')
    source_id = models.CharField(max_length=40, blank=True, default='')
    dedupe_key = models.CharField(max_length=190, unique=True)

    approved_by = models.CharField(max_length=150, blank=True, default='')
    created_by = models.CharField(max_length=150, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['entry_date', 'id']
        verbose_name_plural = 'Leave ledger entries'
        indexes = [
            models.Index(fields=['employee', 'leave_type_code', 'entry_date']),
            models.Index(fields=['remote_employee', 'leave_type_code', 'entry_date']),
        ]

    def __str__(self):
        return f'{self.entry_date} {self.get_kind_display()} {self.days:+} -> {self.balance_after}'


class EmployeeReturnToWork(PersonScopedModel):
    """Coming back from leave (§31).

    Exists because of one specific failure: returning from annual leave was
    being handled by editing the joining date, which destroyed length of
    service, gratuity and every anniversary calculation at once. §97 names it.
    THIS RECORD IS WHAT CHANGES; the joining date is never touched.
    """
    leave_type_code = models.CharField(max_length=30, blank=True, default='')
    leave_start = models.DateField(null=True, blank=True)
    expected_return = models.DateField(null=True, blank=True)
    actual_return = models.DateField(null=True, blank=True)
    delay_days = models.IntegerField(
        null=True, blank=True,
        help_text='Actual minus expected. Stored, not derived, so a later change '
                  'to either date cannot silently rewrite a delay that was '
                  'already actioned.')
    delay_authorised = models.BooleanField(default=False)
    supporting_document = models.ForeignKey(
        'attendance.EmployeeDocument', on_delete=models.SET_NULL, null=True,
        blank=True, related_name='return_to_work_records')
    payroll_effective_date = models.DateField(
        null=True, blank=True,
        help_text='When payroll starts paying them again. Deliberately separate '
                  'from actual_return and from the joining date — §97 forbids '
                  'mixing the legal employment date with the payroll one.')
    attendance_effective_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, default='')
    approved_by = models.CharField(max_length=150, blank=True, default='')
    created_by = models.CharField(max_length=150, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-actual_return', '-id']
        verbose_name_plural = 'Employee return to work'

    def __str__(self):
        return f'{self.person} returned {self.actual_return or "(not yet)"}'


class EmployeeWarning(PersonScopedModel):
    """Disciplinary record — verbal/written/final warning or suspension.

    Every state change (issue, withdraw, acknowledge) is expected to go
    through attendance/services_warnings.py, which audits it — a warning is
    exactly the kind of action that must be answerable later, not a passive
    record like an uploaded document.
    """
    SEVERITY_CHOICES = [
        ('verbal',     'Verbal warning'),
        ('written',    'Written warning'),
        ('final',      'Final warning'),
        ('suspension', 'Suspension'),
    ]
    CATEGORY_CHOICES = [
        ('attendance',  'Attendance'),
        ('conduct',     'Conduct'),
        ('performance', 'Performance'),
        ('policy',      'Policy violation'),
        ('safety',      'Safety'),
        ('other',       'Other'),
    ]
    STATUS_CHOICES = [
        ('active',    'Active'),
        ('expired',   'Expired'),
        ('appealed',  'Appealed'),
        ('withdrawn', 'Withdrawn'),
    ]

    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='other')
    issued_date = models.DateField()
    incident_date = models.DateField(null=True, blank=True)
    description = models.TextField()
    document = models.ForeignKey(
        'attendance.EmployeeDocument', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='warnings')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active', db_index=True)
    valid_until = models.DateField(
        null=True, blank=True,
        help_text='Many policies expire a warning after N months.')
    acknowledged_by_employee = models.BooleanField(default=False)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    issued_by = models.CharField(max_length=150, blank=True, default='')
    notes = models.TextField(blank=True, default='')
    created_by = models.CharField(max_length=150, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-issued_date', '-id']
        indexes = [models.Index(fields=['employee', 'status'])]

    def __str__(self):
        return f'{self.person} — {self.get_severity_display()} ({self.issued_date})'


class EmployeeAsset(PersonScopedModel):
    """Company property issued to an employee — custody, not money.

    Distinct from Recoverable.recoverable_type='asset', which only tracks an
    amount owed for an asset/loan. This is the register of what was actually
    handed over: serial number, condition, issue/return dates. Marking one
    lost is expected to go through attendance/services_assets.py, which links
    a Recoverable so the cost isn't tracked in two disagreeing places.
    """
    ASSET_TYPE_CHOICES = [
        ('laptop',      'Laptop'),
        ('mobile',      'Mobile phone'),
        ('sim',         'SIM card'),
        ('vehicle',     'Vehicle'),
        ('access_card', 'Access card'),
        ('uniform',     'Uniform'),
        ('tool',        'Tool / equipment'),
        ('other',       'Other'),
    ]
    CONDITION_CHOICES = [
        ('new',     'New'),
        ('good',    'Good'),
        ('fair',    'Fair'),
        ('damaged', 'Damaged'),
        ('lost',    'Lost'),
    ]
    STATUS_CHOICES = [
        ('issued',      'Issued'),
        ('returned',    'Returned'),
        ('lost',        'Lost'),
        ('written_off', 'Written off'),
    ]

    asset_type = models.CharField(max_length=20, choices=ASSET_TYPE_CHOICES)
    asset_tag = models.CharField(max_length=60, blank=True, default='')
    description = models.CharField(max_length=200, blank=True, default='')
    serial_number = models.CharField(max_length=100, blank=True, default='')
    value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    condition_at_issue = models.CharField(max_length=20, choices=CONDITION_CHOICES, default='new')
    condition_current = models.CharField(max_length=20, choices=CONDITION_CHOICES, blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='issued', db_index=True)
    issued_date = models.DateField()
    expected_return_date = models.DateField(null=True, blank=True)
    returned_date = models.DateField(null=True, blank=True)
    recoverable = models.ForeignKey(
        'attendance.Recoverable', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='linked_assets',
        help_text='Set when a lost/damaged asset becomes a money-owed ledger row.')
    notes = models.TextField(blank=True, default='')
    issued_by = models.CharField(max_length=150, blank=True, default='')
    created_by = models.CharField(max_length=150, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-issued_date', '-id']
        indexes = [models.Index(fields=['employee', 'status'])]

    def __str__(self):
        return f'{self.person} — {self.get_asset_type_display()} ({self.get_status_display()})'
