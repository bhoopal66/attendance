from django.db import models


class BaseEmployee(models.Model):
    """
    Abstract base model for shared fields between Employee and RemoteEmployee.
    Reduces code duplication and ensures consistency.
    """
    DEPARTMENT_CHOICES = [
        ('Sales', 'Sales'),
        ('Admin', 'Admin'),
    ]
    
    name = models.CharField(max_length=100)
    email = models.EmailField(null=True, blank=True, db_index=True)
    phone = models.CharField(max_length=20, null=True, blank=True)
    department = models.CharField(max_length=100, choices=DEPARTMENT_CHOICES, null=True, blank=True)
    location = models.CharField(max_length=100, null=True, blank=True, 
        help_text="Office location (e.g., 'Dubai HQ', 'Abu Dhabi Branch')")
    team = models.CharField(max_length=100, null=True, blank=True,
        help_text="Team name (e.g., 'Sales', 'Engineering', 'Support')")
    
    # Employment status
    is_active = models.BooleanField(default=True, db_index=True, 
        help_text="Uncheck when employee leaves the company")
    joining_date = models.DateField(null=True, blank=True)
    leaving_date = models.DateField(null=True, blank=True, 
        help_text="Date when employee left the company")
    
    # Portal login credentials
    portal_password = models.CharField(max_length=128, null=True, blank=True,
        help_text="Hashed password for employee portal login")
    
    # Audit timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        abstract = True
    
    def __str__(self):
        return self.name


class Holiday(models.Model):
    """Custom holidays (other than Sundays) that apply to all employees."""
    date = models.DateField(unique=True)
    name = models.CharField(max_length=100)  # e.g., "Christmas", "Eid"
    
    # Audit timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-date']
    
    def __str__(self):
        return f"{self.name} ({self.date})"


class Employee(BaseEmployee):
    """In-house employee tracked via attendance machine."""
    person_id = models.CharField(max_length=50, db_index=True)  # Indexed for lookups
    
    # Additional fields specific to in-house employees
    salary = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    designation = models.CharField(max_length=100, null=True, blank=True)
    
    # Work shift timings (defaults: 10:00-19:00)
    shift_start = models.TimeField(null=True, blank=True, 
        help_text="Expected arrival time (e.g., 09:30)")
    shift_end = models.TimeField(null=True, blank=True, 
        help_text="Expected departure time (e.g., 18:30)")

    class Meta:
        unique_together = ('person_id', 'name')  # Same ID can exist for different people

    def __str__(self):
        return f"{self.name} ({self.person_id})"


class AttendanceRecord(models.Model):
    """Daily attendance record for in-house employees."""
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE)
    date = models.DateField(db_index=True)
    first_in = models.TimeField(null=True, blank=True)
    last_out = models.TimeField(null=True, blank=True)
    work_duration = models.DurationField(null=True, blank=True)
    
    # Audit timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
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
    working_days = models.IntegerField(default=0)  # Days with attendance records
    leave_days = models.IntegerField(default=0)    # Days with no record
    late_days = models.IntegerField(default=0)     # Days with first_in > shift start (before 12:00)
    half_days = models.IntegerField(default=0)     # Days with arrival after 12:00 OR early departure
    
    # Audit timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('employee', 'year', 'month')
        verbose_name_plural = 'Monthly Summaries'

    def __str__(self):
        return f"{self.employee.name} - {self.year}/{self.month}"


class ShiftHistory(models.Model):
    """Tracks shift timing changes for employees over time."""
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='shift_history')
    shift_start = models.TimeField(help_text="Expected arrival time (e.g., 09:30)")
    shift_end = models.TimeField(help_text="Expected departure time (e.g., 18:30)")
    effective_from = models.DateField(help_text="Date from which this shift timing applies")
    
    # Audit timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name_plural = 'Shift Histories'
        ordering = ['-effective_from']  # Most recent first
    
    def __str__(self):
        return f"{self.employee.name}: {self.shift_start}-{self.shift_end} (from {self.effective_from})"


# ============================================
# Remote Employee Models (Call Statistics Based)
# ============================================

class RemoteEmployee(BaseEmployee):
    """Remote employee tracked via phone call statistics."""
    extension_id = models.CharField(max_length=50, db_index=True)  # e.g., "3068"

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
    
    # Call statistics from CSV
    answered_calls = models.IntegerField(default=0)
    no_answered = models.IntegerField(default=0)
    busy = models.IntegerField(default=0)
    failed = models.IntegerField(default=0)
    voicemail = models.IntegerField(default=0)
    
    # Duration fields stored as timedelta
    total_ring_duration = models.DurationField(null=True, blank=True)
    total_talk_duration = models.DurationField(null=True, blank=True)
    
    # Calculated attendance status
    attendance_status = models.CharField(max_length=20, choices=ATTENDANCE_STATUS_CHOICES, default='absent')
    
    # Audit timestamps
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
            return 'present'  # Or could be marked differently
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
        # Auto-calculate attendance status before saving
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
    
    # Aggregated call stats
    total_calls = models.IntegerField(default=0)
    total_talk_time = models.DurationField(null=True, blank=True)
    
    # Audit timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('employee', 'year', 'month')
        verbose_name = 'Remote Monthly Summary'
        verbose_name_plural = 'Remote Monthly Summaries'
        ordering = ['-year', '-month', 'employee__name']

    def __str__(self):
        return f"{self.employee.name} - {self.year}/{self.month}"


class EarlyLeaveRequest(models.Model):
    """Request for early leave (field visits, customer meetings, etc.)."""
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]
    
    # Can be either in-house or remote employee
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, null=True, blank=True)
    remote_employee = models.ForeignKey(RemoteEmployee, on_delete=models.CASCADE, null=True, blank=True)
    
    # Request details
    request_date = models.DateField(help_text="Date of early leave")
    leaving_time = models.TimeField(help_text="Time when leaving the office")
    return_time = models.TimeField(null=True, blank=True, help_text="Estimated time of return")
    destination = models.CharField(max_length=255, help_text="Where they are going")
    customer_name = models.CharField(max_length=255, help_text="Customer/Client they are meeting")
    reason = models.TextField(blank=True, help_text="Additional details or reason")
    
    # Status tracking
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    admin_notes = models.TextField(blank=True, help_text="Admin comments on the request")
    
    # Approved times - stored for merging with biometric data during upload
    approved_first_in = models.TimeField(null=True, blank=True, 
        help_text="Admin-approved first in time (for merging with biometric data)")
    approved_last_out = models.TimeField(null=True, blank=True,
        help_text="Admin-approved last out time (for merging with biometric data)")
    
    # Timestamps
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
    
    # Employee (in-house only for now)
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='leave_requests')
    
    # Leave details
    leave_type = models.CharField(max_length=20, choices=LEAVE_TYPE_CHOICES)
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField(help_text="Reason for leave request")
    
    # Document upload (required for sick/medical leave)
    document = models.FileField(
        upload_to='leave_documents/%Y/%m/', 
        null=True, 
        blank=True,
        help_text="Required for Sick and Medical leave"
    )
    
    # Calculated and admin-editable days
    requested_days = models.PositiveIntegerField(help_text="Number of days requested")
    approved_days = models.PositiveIntegerField(
        null=True, 
        blank=True,
        help_text="Number of days approved (can be less than requested)"
    )
    
    # Status tracking
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    admin_notes = models.TextField(blank=True, help_text="Admin comments or rejection reason")
    
    # Timestamps
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
    
    def save(self, *args, **kwargs):
        # Auto-calculate requested_days if not set
        if not self.requested_days:
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

