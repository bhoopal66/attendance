"""
Payroll models for salary adjustments and DSA bank submissions.
"""

from django.core.exceptions import ValidationError
from django.db import models
from attendance.models import Employee


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
    Each bank has a fixed per-account commission charge.
    """
    name = models.CharField(max_length=100, unique=True)
    per_account_charge = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text="Commission per account submission (AED)"
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} (AED {self.per_account_charge}/account)"


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
