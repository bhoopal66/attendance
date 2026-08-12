"""
Payroll models for salary adjustments, DSA bank submissions, and deduction tracking.
"""

from decimal import Decimal

from django.conf import settings
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
    ('other_addition', 'Others'),
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
    and optionally in INR (for Indian employees) and NPR (for Nepalese employees).
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
    npr_per_account_charge = models.DecimalField(
        max_digits=10, decimal_places=2,
        null=True, blank=True,
        help_text="Commission per account submission (NPR) — for Nepalese employees. Leave blank if not applicable."
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
        if currency == 'NPR' and self.npr_per_account_charge:
            return self.npr_per_account_charge
        return self.per_account_charge

    def __str__(self):
        inr_part = f" / INR {self.inr_per_account_charge}/account" if self.inr_per_account_charge else ""
        npr_part = f" / NPR {self.npr_per_account_charge}/account" if self.npr_per_account_charge else ""
        return f"{self.name} (AED {self.per_account_charge}/account{inr_part}{npr_part})"


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
    currency = models.CharField(
        max_length=3, choices=Employee.CURRENCY_CHOICES, default='AED',
        help_text="Currency total_amount is recorded in. Set from the employee's "
                   "currency at creation; if the employee's currency later changes, "
                   "still-outstanding entries are converted and re-tagged, but entries "
                   "already fully recovered keep the currency they were recovered in.",
    )
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


class DeductionCarryover(models.Model):
    """
    When an employee's final salary would go negative, the overflow is capped
    at zero and carried into the following month as an additional deduction.
    """
    employee = models.ForeignKey(
        'attendance.Employee',
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='deduction_carryovers',
    )
    remote_employee = models.ForeignKey(
        'attendance.RemoteEmployee',
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='deduction_carryovers',
    )
    from_year = models.IntegerField()
    from_month = models.IntegerField()
    to_year = models.IntegerField()
    to_month = models.IntegerField()
    currency = models.CharField(
        max_length=3, choices=Employee.CURRENCY_CHOICES, default='AED',
        help_text="Currency overflow_amount/applied_amount are recorded in. Set "
                   "from the employee's currency when the carryover is created; "
                   "still-open carryovers are converted and re-tagged if the "
                   "employee's currency later changes, but already-cleared ones "
                   "keep the currency they were recovered in.",
    )
    overflow_amount = models.DecimalField(max_digits=10, decimal_places=2)
    applied_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    is_skipped = models.BooleanField(default=False, help_text="Admin waived this month's carried-over deduction; it is excluded from live totals.")
    skipped_at = models.DateTimeField(null=True, blank=True)
    skipped_by = models.CharField(max_length=150, blank=True)
    skip_reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['employee', 'from_year', 'from_month'],
                condition=models.Q(employee__isnull=False),
                name='unique_inhouse_carryover_month',
            ),
            models.UniqueConstraint(
                fields=['remote_employee', 'from_year', 'from_month'],
                condition=models.Q(remote_employee__isnull=False),
                name='unique_remote_carryover_month',
            ),
        ]

    def __str__(self):
        emp = self.employee or self.remote_employee
        return f"{emp.name} overflow {self.from_month}/{self.from_year} → {self.to_month}/{self.to_year}: {self.overflow_amount}"


class ExchangeRate(models.Model):
    """
    Stores the exchange rate on the 10th of each month.
    Rate is expressed as: 1 AED = <rate> units of the foreign currency.
    E.g. for INR: rate=22.50 means 1 AED = 22.50 INR.
    To convert foreign currency to AED: amount / rate.
    """
    currency = models.CharField(max_length=3, help_text="Foreign currency code, e.g. INR")
    year = models.IntegerField()
    month = models.IntegerField(help_text="1-12")
    rate = models.DecimalField(max_digits=12, decimal_places=4, help_text="1 AED = this many units of the foreign currency")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('currency', 'year', 'month')
        ordering = ['-year', '-month']

    def __str__(self):
        return f"1 AED = {self.rate} {self.currency} ({self.month}/{self.year})"


class CommissionTierSettings(models.Model):
    """
    Tiered DSA commission rule for a foreign currency (e.g. INR, NPR).
    The first `threshold` bank-submission accounts (summed across all banks,
    in bank-name order) are paid at each bank's per-account rate for that
    currency; every account beyond the threshold is paid a flat `overflow_rate`
    instead. See Bank.charge_for_currency() for the per-bank rate lookup.
    """
    currency = models.CharField(max_length=3, unique=True, help_text="Foreign currency code, e.g. INR, NPR")
    threshold = models.PositiveIntegerField(
        default=4,
        help_text="Number of accounts (across all banks) paid at each bank's per-account rate before overflow applies"
    )
    overflow_rate = models.DecimalField(
        max_digits=12, decimal_places=2,
        help_text="Flat commission per account once the threshold is exceeded"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Commission Tier Setting"
        verbose_name_plural = "Commission Tier Settings"
        ordering = ['currency']

    def __str__(self):
        return f"{self.currency}: first {self.threshold} at bank rate, then {self.overflow_rate}/account"


class GeneratedDocument(models.Model):
    """
    Registry of every payslip and payment voucher generated by the system.
    Each record gets a stable unique ID (PS-XXXXX / PV-XXXXX) that can be
    used to search and re-print any document ever produced.
    """
    DOC_TYPES = [
        ('payslip', 'Payslip'),
        ('advance_voucher', 'Advance Payment Voucher'),
    ]
    doc_type = models.CharField(max_length=20, choices=DOC_TYPES)
    employee = models.ForeignKey(
        'attendance.Employee',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='generated_documents',
    )
    remote_employee = models.ForeignKey(
        'attendance.RemoteEmployee',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='generated_documents',
    )
    year = models.IntegerField()
    month = models.IntegerField(help_text="1–12")
    deduction_entry = models.ForeignKey(
        DeductionEntry,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='generated_vouchers',
        help_text="Populated for advance vouchers only",
    )
    generated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-generated_at']
        constraints = [
            models.UniqueConstraint(
                fields=['doc_type', 'employee', 'year', 'month'],
                condition=models.Q(employee__isnull=False, deduction_entry__isnull=True),
                name='unique_payslip_inhouse',
            ),
            models.UniqueConstraint(
                fields=['doc_type', 'remote_employee', 'year', 'month'],
                condition=models.Q(remote_employee__isnull=False, deduction_entry__isnull=True),
                name='unique_payslip_remote',
            ),
            models.UniqueConstraint(
                fields=['deduction_entry', 'year', 'month'],
                condition=models.Q(deduction_entry__isnull=False),
                name='unique_voucher_entry_month',
            ),
        ]

    @property
    def ref(self):
        """Human-readable reference number, e.g. PS-00042 or PV-00017."""
        prefix = 'PV' if self.doc_type == 'advance_voucher' else 'PS'
        return f'{prefix}-{self.id:05d}'

    def __str__(self):
        emp = self.employee or self.remote_employee
        return f"{self.ref} — {emp.name if emp else '?'} {self.month}/{self.year}"


class FrozenPayrollMonth(models.Model):
    """
    Immutable snapshot of a fully-computed payroll month.
    Once a month is frozen, the dashboard serves from this snapshot instead of
    recalculating — so future changes to employee settings, bank rates, or
    attendance summaries don't alter historical payroll figures.
    """
    year = models.IntegerField()
    month = models.IntegerField(help_text="1-12")
    frozen_at = models.DateTimeField()
    frozen_by = models.CharField(max_length=150, blank=True, help_text="Username of the admin who froze this month")
    snapshot = models.JSONField(help_text="Full serialised payroll context for this month")

    class Meta:
        unique_together = [('year', 'month')]
        ordering = ['-year', '-month']

    def __str__(self):
        return f"Frozen payroll {self.month}/{self.year} (by {self.frozen_by})"


class PaidSalaryRecord(models.Model):
    """
    Per-employee immutable payroll snapshot once payment is confirmed.
    All values (attendance, deductions, commission, final salary) are locked
    at the moment of marking as paid and never recalculated, regardless of
    subsequent changes to attendance, employee settings, bank rates, or deductions.
    """
    employee = models.ForeignKey(
        'attendance.Employee', null=True, blank=True, on_delete=models.CASCADE,
        related_name='paid_salary_records',
    )
    remote_employee = models.ForeignKey(
        'attendance.RemoteEmployee', null=True, blank=True, on_delete=models.CASCADE,
        related_name='paid_salary_records',
    )
    year = models.IntegerField()
    month = models.IntegerField(help_text="1-12")
    final_salary = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=10, default='AED')
    paid_at = models.DateTimeField()
    paid_by = models.CharField(max_length=150, blank=True)
    snapshot = models.JSONField(
        null=True, blank=True,
        help_text="Full payroll snapshot at time of payment: attendance, deductions, commission, bank submissions, final salary",
    )

    class Meta:
        ordering = ['-year', '-month']

    def __str__(self):
        emp = self.employee or self.remote_employee
        return f"Paid {self.month}/{self.year} — {emp.name if emp else '?'} ({self.final_salary} {self.currency})"
