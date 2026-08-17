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
