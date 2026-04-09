import logging
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models

logger = logging.getLogger('attendance')

tcr_id_validator = RegexValidator(
    regex=r'^TCR\d+$',
    message='TCR ID must be in the format TCR followed by digits (e.g. TCR1000224).'
)


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

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if self.leaving_date and self.joining_date and self.leaving_date < self.joining_date:
            raise ValidationError({
                'leaving_date': "Leaving date cannot be before joining date."
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
    A named date range during which all in-house employees work different hours.
    e.g., Ramadan working hours (9:00 AM - 4:00 PM) for a specific date range.

    During this period, the special shift overrides each employee's normal shift
    when computing attendance status in the report.
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

    class Meta:
        unique_together = ('person_id', 'name')

    def __str__(self):
        return f"{self.name} ({self.person_id})"


class AttendanceRecord(models.Model):
    """Daily attendance record for in-house employees."""
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE)
    date = models.DateField(db_index=True)
    first_in = models.TimeField(null=True, blank=True)
    last_out = models.TimeField(null=True, blank=True)
    work_duration = models.DurationField(null=True, blank=True)

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
# Remote Employee Models (Call Statistics Based)
# ============================================

class RemoteEmployee(BaseEmployee):
    """Remote employee tracked via phone call statistics."""
    extension_id = models.CharField(max_length=50, db_index=True)

    salary = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    designation = models.CharField(max_length=100, null=True, blank=True)

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

    def calculate_attendance_status(self):
        """
        Calculate attendance status based on talk duration and day of week.

        Rules:
        - Mon-Thu: <45min=Absent, 45-89min=Half Day, >=90min=Present
        - Friday: <30min=Absent, 30-59min=Half Day, >=60min=Present
        - Saturday: <=20min=Absent, 21-44min=Half Day, >=45min=Present
        - Sunday: Holiday (no attendance calculation)
        """
        if not self.total_talk_duration:
            return 'absent'

        weekday = self.date.weekday()  # 0=Monday, 6=Sunday
        talk_minutes = self.total_talk_duration.total_seconds() / 60

        if weekday == 6:  # Sunday - Holiday
            return 'present'
        elif weekday == 5:  # Saturday
            if talk_minutes >= 45:
                return 'present'
            elif talk_minutes >= 21:
                return 'half_day'
            else:
                return 'absent'
        elif weekday == 4:  # Friday
            if talk_minutes >= 60:
                return 'present'
            elif talk_minutes >= 30:
                return 'half_day'
            else:
                return 'absent'
        else:  # Monday-Thursday
            if talk_minutes >= 90:
                return 'present'
            elif talk_minutes >= 45:
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

    is_paid = models.BooleanField(
        default=True,
        help_text="Whether the employee is paid during this annual leave period"
    )
    salary_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, default=100,
        help_text="Percentage of normal salary paid during leave (0–100). Only relevant when is_paid=True."
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
