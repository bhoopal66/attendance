"""
Payroll models for salary adjustments, DSA bank submissions, and deduction tracking.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from attendance.models import Employee


DEDUCTION_CATEGORY_CHOICES = [
    # Deductions
    ('advance', 'Advance'),
    ('visa_status_change', 'Visa Status Change'),
    ('clawback', 'Clawback'),
    ('leave_deduction', 'Leave Deduction'),
    ('late_deduction', 'Late Deduction'),
    ('other_deduction', 'Other Deduction'),
    # Additions
    ('last_month_balance', 'Last Month Balance'),
    ('paid_leave', 'Paid Leave'),
]

_DEDUCTION_CATS = {
    'advance', 'visa_status_change', 'clawback', 'leave_deduction',
    'late_deduction', 'other_deduction',
}


class PayrollAdjustment(models.Model):
    """
    Monthly adjustments (incentives/reductions) for employee payroll.
    Each adjustment is per-employee, per-month with a reason.
    Supports both in-house (Employee) and remote (RemoteEmployee) employees.
    """
    ADJUSTMENT_TYPES = [
        ('incentive', 'Incentive'),
        ('reduction', 'Reduction'),
    ]

    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='payroll_adjustments'
    )
    remote_employee = models.ForeignKey(
        'attendance.RemoteEmployee',
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='payroll_adjustments'
    )
    year = models.IntegerField()
    month = models.IntegerField(help_text="1-12")
    adjustment_type = models.CharField(max_length=20, choices=ADJUSTMENT_TYPES)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    reason = models.TextField(help_text="Reason for the adjustment")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['employee', 'year', 'month']),
            models.Index(fields=['remote_employee', 'year', 'month']),
        ]

    def __str__(self):
        emp = self.employee or self.remote_employee
        sign = '+' if self.adjustment_type == 'incentive' else '-'
        return f"{emp.name} {self.year}/{self.month}: {sign}{self.amount}"

    def clean(self):
        super().clean()
        if self.employee and self.remote_employee:
            raise ValidationError(
                "An adjustment must be linked to either an in-house employee or a remote employee, not both."
            )
        if not self.employee and not self.remote_employee:
            raise ValidationError("An adjustment must be linked to an employee.")


class Bank(models.Model):
    """
    A bank that DSA agents submit accounts to.
    Each bank has a per-account commission charge in AED (for UAE employees)
    and optionally in INR (for Indian employees).
    """
    name = models.CharField(max_length=100, unique=True)
    per_account_charge = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text="Commission per account submission (AED)"
    )
    inr_per_account_charge = models.DecimalField(
        max_digits=10, decimal_places=2,
        null=True, blank=True,
        help_text="Commission per account submission (INR) — for Indian employees. Leave blank if not applicable."
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def charge_for_currency(self, currency):
        """Return the appropriate per-account charge based on employee currency."""
        if currency == 'INR' and self.inr_per_account_charge:
            return self.inr_per_account_charge
        return self.per_account_charge

    def __str__(self):
        inr_part = f" / INR {self.inr_per_account_charge}/account" if self.inr_per_account_charge else ""
        return f"{self.name} (AED {self.per_account_charge}/account{inr_part})"


class BankSubmission(models.Model):
    """
    Monthly account submission count for a DSA agent at a specific bank.
    Commission = submission_count × bank.per_account_charge
    """
    employee = models.ForeignKey(
        'attendance.Employee',
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='bank_submissions'
    )
    remote_employee = models.ForeignKey(
        'attendance.RemoteEmployee',
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='bank_submissions'
    )
    bank = models.ForeignKey(Bank, on_delete=models.CASCADE, related_name='submissions')
    year = models.IntegerField()
    month = models.IntegerField(help_text="1-12")
    submission_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['bank__name']
        indexes = [
            models.Index(fields=['employee', 'year', 'month']),
            models.Index(fields=['remote_employee', 'year', 'month']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['employee', 'bank', 'year', 'month'],
                condition=models.Q(employee__isnull=False),
                name='unique_inhouse_bank_month',
            ),
            models.UniqueConstraint(
                fields=['remote_employee', 'bank', 'year', 'month'],
                condition=models.Q(remote_employee__isnull=False),
                name='unique_remote_bank_month',
            ),
        ]

    @property
    def commission(self):
        return self.submission_count * self.bank.per_account_charge

    def __str__(self):
        emp = self.employee or self.remote_employee
        return f"{emp.name} — {self.bank.name} {self.year}/{self.month}: {self.submission_count} submissions"

    def clean(self):
        super().clean()
        if self.employee and self.remote_employee:
            raise ValidationError("A submission must be linked to either an in-house or remote employee, not both.")
        if not self.employee and not self.remote_employee:
            raise ValidationError("A submission must be linked to an employee.")


class DeductionEntry(models.Model):
    """
    Tracks deductions and additions per employee, with optional split over multiple months.
    Categories:
      Deductions — advance, visa_cost, clawback, leave_deduction, late_deduction,
                   closed_account, other_deduction
      Additions  — last_month_balance, paid_leave, gratuity
    If split_months > 1, the total_amount is divided equally across that many consecutive
    months starting from start_year/start_month.
    """
    employee = models.ForeignKey(
        'attendance.Employee',
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='deduction_entries',
    )
    remote_employee = models.ForeignKey(
        'attendance.RemoteEmployee',
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='deduction_entries',
    )
    category = models.CharField(max_length=30, choices=DEDUCTION_CATEGORY_CHOICES)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    split_months = models.PositiveIntegerField(default=1, help_text="Spread deduction over N months")
    start_year = models.IntegerField()
    start_month = models.IntegerField(help_text="1-12")
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['employee', 'start_year', 'start_month']),
            models.Index(fields=['remote_employee', 'start_year', 'start_month']),
        ]

    @property
    def entry_type(self):
        return 'deduction' if self.category in _DEDUCTION_CATS else 'addition'

    @property
    def installment_amount(self):
        """Per-month amount (total ÷ split_months, rounded to 2dp)."""
        if self.split_months <= 1:
            return self.total_amount
        from decimal import ROUND_HALF_UP
        return (self.total_amount / Decimal(self.split_months)).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )

    def end_month_year(self):
        """Returns (year, month) of the last installment month."""
        end_idx = self.start_year * 12 + (self.start_month - 1) + self.split_months - 1
        y, m = divmod(end_idx, 12)
        return y, m + 1

    def is_active_in(self, year, month):
        """True if this entry contributes an installment in the given month."""
        start_idx = self.start_year * 12 + (self.start_month - 1)
        target_idx = year * 12 + (month - 1)
        return start_idx <= target_idx < start_idx + self.split_months

    def clean(self):
        super().clean()
        if self.employee and self.remote_employee:
            raise ValidationError("A deduction must be linked to either an in-house or remote employee, not both.")
        if not self.employee and not self.remote_employee:
            raise ValidationError("A deduction must be linked to an employee.")
        if self.split_months < 1:
            raise ValidationError("Split months must be at least 1.")

    def __str__(self):
        emp = self.employee or self.remote_employee
        return f"{emp.name} — {self.get_category_display()} {self.start_year}/{self.start_month}: {self.total_amount}"
