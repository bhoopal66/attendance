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
    # Phase 10 — revenue earned by the company per funded account at this bank
    revenue_per_account = models.DecimalField(
        max_digits=10, decimal_places=2,
        null=True, blank=True,
        help_text="Revenue earned by the company per account submitted to this bank (AED). "
                  "Used for performance & profitability reporting. Leave blank if not tracked.",
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
    recoverable = models.ForeignKey(
        'attendance.Recoverable',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='deduction_entries',
        help_text='Link this deduction to a Recoverable sub-ledger row (optional). '
                  'Phase 9 will use this to auto-update amount_recovered.',
    )
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


# ============================================================
# Phase 9 — Payroll Lifecycle (PayrollRun status machine)
# ============================================================

class PayrollRun(models.Model):
    """
    One row per calendar month — tracks the lifecycle of that month's payroll
    from initial preparation through to final posting.

    Status machine:
        draft → review → approved → locked → paid → posted

    The existing FrozenPayrollMonth and PaidSalaryRecord tables are NOT replaced
    by this model; they remain the authoritative snapshot stores. PayrollRun is
    the lifecycle control layer that sits above them.

    Existing frozen / paid months are backfilled on first migration so history
    shows the correct stage immediately.
    """

    STATUS_DRAFT    = 'draft'
    STATUS_REVIEW   = 'review'
    STATUS_APPROVED = 'approved'
    STATUS_LOCKED   = 'locked'
    STATUS_PAID     = 'paid'
    STATUS_POSTED   = 'posted'

    STATUS_CHOICES = [
        (STATUS_DRAFT,    'Draft'),
        (STATUS_REVIEW,   'Under Review'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_LOCKED,   'Locked'),
        (STATUS_PAID,     'Paid'),
        (STATUS_POSTED,   'Posted'),
    ]

    # Legal forward-only transitions
    TRANSITIONS = {
        STATUS_DRAFT:    STATUS_REVIEW,
        STATUS_REVIEW:   STATUS_APPROVED,
        STATUS_APPROVED: STATUS_LOCKED,
        STATUS_LOCKED:   STATUS_PAID,
        STATUS_PAID:     STATUS_POSTED,
    }

    # Human label for each forward action button
    TRANSITION_LABELS = {
        STATUS_DRAFT:    'Submit for Review',
        STATUS_REVIEW:   'Approve',
        STATUS_APPROVED: 'Lock Payroll',
        STATUS_LOCKED:   'Mark as Paid',
        STATUS_PAID:     'Post to Finance',
    }

    year  = models.IntegerField()
    month = models.IntegerField(help_text='1–12')
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT, db_index=True,
    )

    # Stage audit trail — populated as each transition fires
    prepared_by  = models.CharField(max_length=150, blank=True)
    prepared_at  = models.DateTimeField(null=True, blank=True)
    reviewed_by  = models.CharField(max_length=150, blank=True)
    reviewed_at  = models.DateTimeField(null=True, blank=True)
    approved_by  = models.CharField(max_length=150, blank=True)
    approved_at  = models.DateTimeField(null=True, blank=True)
    locked_by    = models.CharField(max_length=150, blank=True)
    locked_at    = models.DateTimeField(null=True, blank=True)
    paid_at      = models.DateTimeField(null=True, blank=True)
    paid_by      = models.CharField(max_length=150, blank=True)
    posted_by    = models.CharField(max_length=150, blank=True)
    posted_at    = models.DateTimeField(null=True, blank=True)

    notes = models.TextField(blank=True, help_text='Free-text notes visible to all payroll staff')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('year', 'month')]
        ordering = ['-year', '-month']
        verbose_name = 'Payroll Run'
        verbose_name_plural = 'Payroll Runs'

    def __str__(self):
        month_name = [
            '', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
            'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
        ][self.month]
        return f"Payroll Run {month_name} {self.year} [{self.get_status_display()}]"

    # ── Helpers ──────────────────────────────────────────────────────────────

    @classmethod
    def get_or_create_for_month(cls, year, month):
        """Return the PayrollRun for the given month, creating a Draft if absent."""
        obj, _ = cls.objects.get_or_create(year=year, month=month)
        return obj

    @property
    def next_status(self):
        """The status this run would move to on the next forward transition, or None."""
        return self.TRANSITIONS.get(self.status)

    @property
    def next_action_label(self):
        """Button label for the next forward transition, or None if fully posted."""
        return self.TRANSITION_LABELS.get(self.status)

    @property
    def is_editable(self):
        """Payroll figures may only be changed while the run is in Draft or Review."""
        return self.status in (self.STATUS_DRAFT, self.STATUS_REVIEW)

    @property
    def is_locked_or_beyond(self):
        """True once the month is locked — dashboard switches to snapshot."""
        return self.status in (self.STATUS_LOCKED, self.STATUS_PAID, self.STATUS_POSTED)

    @property
    def status_order(self):
        """Numeric position in the lifecycle (0 = draft … 5 = posted)."""
        order = [
            self.STATUS_DRAFT, self.STATUS_REVIEW, self.STATUS_APPROVED,
            self.STATUS_LOCKED, self.STATUS_PAID, self.STATUS_POSTED,
        ]
        try:
            return order.index(self.status)
        except ValueError:
            return 0

    def advance(self, user_username):
        """
        Move the run one step forward. Stamps the appropriate audit field.
        Returns (True, new_status) on success or (False, error_message) on failure.
        """
        from django.utils import timezone
        nxt = self.next_status
        if nxt is None:
            return False, 'This payroll run is already fully posted.'
        prev = self.status
        self.status = nxt
        now = timezone.now()
        if prev == self.STATUS_DRAFT:
            self.prepared_by = user_username
            self.prepared_at = now
        elif prev == self.STATUS_REVIEW:
            self.reviewed_by = user_username
            self.reviewed_at = now
        elif prev == self.STATUS_APPROVED:
            self.approved_by = user_username
            self.approved_at = now
        elif prev == self.STATUS_LOCKED:
            self.locked_by  = user_username
            self.locked_at  = now
        elif prev == self.STATUS_PAID:
            self.paid_by = user_username
            self.paid_at = now
        elif prev == self.STATUS_POSTED:
            self.posted_by = user_username
            self.posted_at = now
        self.save()

        # Phase 13 — audit trail. Never let a logging failure block the
        # transition itself; log_audit() already swallows its own errors.
        try:
            from attendance.audit import log_audit
            from attendance.models import AuditLog
            log_audit(
                actor=user_username,
                action=AuditLog.ACTION_TRANSITION,
                instance=self,
                note=f'{prev} -> {nxt}',
            )
        except Exception:
            pass

        return True, nxt


class EmployeeTarget(models.Model):
    """
    Phase 10 — Monthly funded-accounts target for a DSA agent.

    One target per employee per month (in-house or remote, dual-FK pattern
    mirroring BankSubmission).  Achievement is derived at read time from
    BankSubmission counts for the same month — no duplicated data.
    """
    employee = models.ForeignKey(
        'attendance.Employee',
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='monthly_targets',
    )
    remote_employee = models.ForeignKey(
        'attendance.RemoteEmployee',
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='monthly_targets',
    )
    year = models.IntegerField()
    month = models.IntegerField(help_text='1–12')
    target_accounts = models.PositiveIntegerField(
        default=0,
        help_text='Number of funded accounts targeted for the month.',
    )
    notes = models.CharField(max_length=255, blank=True)

    created_by = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_by = models.CharField(max_length=150, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-year', '-month']
        verbose_name = 'Employee Target'
        verbose_name_plural = 'Employee Targets'
        indexes = [
            models.Index(fields=['employee', 'year', 'month'],
                         name='pay__tgt_emp_ym_idx'),
            models.Index(fields=['remote_employee', 'year', 'month'],
                         name='pay__tgt_remp_ym_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['employee', 'year', 'month'],
                condition=models.Q(employee__isnull=False),
                name='unique_inhouse_target_month',
            ),
            models.UniqueConstraint(
                fields=['remote_employee', 'year', 'month'],
                condition=models.Q(remote_employee__isnull=False),
                name='unique_remote_target_month',
            ),
        ]

    # ── linked person helpers ─────────────────────────────────────────────────
    @property
    def person(self):
        return self.employee or self.remote_employee

    @property
    def person_label(self):
        p = self.person
        return p.name if p else '?'

    # ── achievement (derived from BankSubmission) ─────────────────────────────
    def achieved_accounts(self):
        """Total funded accounts submitted by this person in the target month."""
        qs = BankSubmission.objects.filter(year=self.year, month=self.month)
        if self.employee_id:
            qs = qs.filter(employee_id=self.employee_id)
        elif self.remote_employee_id:
            qs = qs.filter(remote_employee_id=self.remote_employee_id)
        else:
            return 0
        agg = qs.aggregate(total=models.Sum('submission_count'))
        return agg['total'] or 0

    def achieved_revenue(self):
        """
        Derived revenue (AED) = Σ submission_count × bank.revenue_per_account
        across all banks for the target month.  Banks with no revenue rate
        contribute zero.
        """
        qs = BankSubmission.objects.filter(
            year=self.year, month=self.month,
        ).select_related('bank')
        if self.employee_id:
            qs = qs.filter(employee_id=self.employee_id)
        elif self.remote_employee_id:
            qs = qs.filter(remote_employee_id=self.remote_employee_id)
        else:
            return Decimal('0')
        total = Decimal('0')
        for sub in qs:
            rate = sub.bank.revenue_per_account
            if rate:
                total += sub.submission_count * rate
        return total

    def achievement_pct(self):
        """Achievement percentage (0 if no target set)."""
        if not self.target_accounts:
            return None
        return round(self.achieved_accounts() * 100 / self.target_accounts, 1)

    def clean(self):
        super().clean()
        if self.employee and self.remote_employee:
            raise ValidationError(
                'A target must be linked to either an in-house or remote employee, not both.'
            )
        if not self.employee and not self.remote_employee:
            raise ValidationError(
                'A target must be linked to an in-house or remote employee.'
            )
        if self.month < 1 or self.month > 12:
            raise ValidationError('Month must be between 1 and 12.')

    def __str__(self):
        return f'{self.person_label} — {self.year}/{self.month:02d}: {self.target_accounts} accounts'
