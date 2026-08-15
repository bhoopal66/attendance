"""
Payroll models for salary adjustments, DSA bank submissions, and deduction tracking.
"""

import re
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from attendance.models import Employee


PAYMENT_METHOD_CHOICES = [
    ('wps', 'WPS'),
    ('bank_transfer', 'Bank Transfer'),
    ('cash', 'Cash'),
    # Not directly selectable — derived automatically when an employee's
    # disbursement is split across more than one of the methods above.
    ('mixed', 'Multiple Methods'),
]

# Deduction categories that the Phase E6 detailed table groups under a single
# "Other" column. Kept here next to the category list itself so the two can
# never drift apart: every category must appear in exactly one displayed column,
# or the itemized columns stop reconciling to the Deductions total.
OTHER_DEDUCTION_CATEGORIES = ['visa_status_change', 'clawback', 'other_deduction']

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


# ---------------------------------------------------------------------------
# Phase 2 - Deduction Master lookup helpers
# ---------------------------------------------------------------------------
# The three constants above stay exactly as they are. They are the FALLBACK,
# and they are what every existing `from .models import ...` still gets, so
# nothing breaks if this module is imported before the DeductionType table
# exists (fresh database, or `migrate` mid-flight).
#
# Callers that want the *configurable* list call the functions below instead.
# Each one reads the DeductionType table and falls back to the constant if the
# table is missing or empty. That fallback is what makes this phase safe: with
# an unseeded table, every function returns precisely today's behaviour.
#
# CACHING - read this before changing it.
# `DeductionEntry.entry_type` is evaluated once per entry inside payroll loops,
# so a query per call is not acceptable. The map is cached in-process with a
# short TTL and is invalidated immediately in the worker that saves a type.
# Under multiple gunicorn workers a *sibling* worker can therefore serve a
# stale map for up to _TYPE_CACHE_TTL seconds after a type is added or edited.
# The practical effect is limited to a brand-new custom type briefly rendering
# with the wrong deduction/addition styling; no stored amount is affected, and
# the nine system types never change. Lowering the TTL costs queries; removing
# the cache costs a query per deduction entry per page render.

import time as _time

_TYPE_CACHE_TTL = 30.0
_type_cache = {'at': 0.0, 'rows': None}

# Field order of the cached tuples. Kept as one constant so the several
# unpackings below cannot drift out of step with the query.
_TYPE_FIELDS = ('code', 'name', 'entry_type', 'is_active',
                'allow_manual_entry', 'rolls_up_to_other', 'colour')


def _deduction_type_rows(force=False):
    """All DeductionType rows as plain tuples, cached. Never raises."""
    now = _time.monotonic()
    if not force and _type_cache['rows'] is not None and now - _type_cache['at'] < _TYPE_CACHE_TTL:
        return _type_cache['rows']
    try:
        rows = list(
            DeductionType.objects.order_by('sort_order', 'name')
            .values_list(*_TYPE_FIELDS)
        )
    except Exception:
        # Table not created yet (initial migrate), or the database is
        # unavailable. Callers fall back to the module constants.
        return []
    _type_cache['rows'] = rows
    _type_cache['at'] = now
    return rows


def invalidate_deduction_type_cache():
    """Drop the cached type map in this worker. Called from DeductionType.save()."""
    _type_cache['rows'] = None
    _type_cache['at'] = 0.0


def _fallback_rows():
    """The nine hard-coded categories shaped like DeductionType rows."""
    return [
        (code, label,
         'deduction' if code in _DEDUCTION_CATS else 'addition',
         True,
         True,   # allow_manual_entry - today's form offers every category
         code in OTHER_DEDUCTION_CATEGORIES,
         '')
        for code, label in DEDUCTION_CATEGORY_CHOICES
    ]


def deduction_category_choices(include_inactive=False):
    """(code, label) for every configured type - the configurable replacement
    for DEDUCTION_CATEGORY_CHOICES. Falls back to the constant when unseeded."""
    rows = _deduction_type_rows() or _fallback_rows()
    return [(c, n) for c, n, _t, active, _m, _r, _col in rows
            if include_inactive or active]


def manual_deduction_choices():
    """Types a user may pick by hand.

    A type with allow_manual_entry off is excluded. That flag is meant for
    attendance-derived types (leave, late) which payroll computes itself, where
    a manual entry deducts the same absence twice - but it ships ON for all
    nine seeded types so that this phase changes nothing, and is the operator's
    to turn off.
    """
    rows = _deduction_type_rows() or _fallback_rows()
    return [(c, n) for c, n, _t, active, manual, _r, _col in rows
            if active and manual]


def deduction_type_groups(manual_only=True):
    """[(group_label, [(code, label), ...]), ...] for grouped <select> menus."""
    rows = _deduction_type_rows() or _fallback_rows()
    ded, add = [], []
    for code, name, etype, active, manual, _r, _col in rows:
        if not active or (manual_only and not manual):
            continue
        (ded if etype == 'deduction' else add).append((code, name))
    out = []
    if ded:
        out.append(('Deductions', ded))
    if add:
        out.append(('Additions', add))
    return out


def deduction_codes():
    """Set of codes that reduce net pay - the configurable _DEDUCTION_CATS."""
    rows = _deduction_type_rows()
    if not rows:
        return set(_DEDUCTION_CATS)
    return {c for c, _n, t, _a, _m, _r, _col in rows if t == 'deduction'}


def valid_deduction_codes():
    """Every acceptable value for DeductionEntry.category, active or not.

    Deliberately includes inactive types: deactivating a type must stop new
    entries being created - which the form does - but must not invalidate the
    historical entries that already carry that code.
    """
    rows = _deduction_type_rows() or _fallback_rows()
    return {c for c, _n, _t, _a, _m, _r, _col in rows}


def other_rollup_codes():
    """Codes the dashboard shows inside the single "Other" deductions column.

    The configurable replacement for OTHER_DEDUCTION_CATEGORIES. Every
    deduction code must appear in exactly one displayed column or the itemized
    columns stop summing to the Deductions total, which is why any type without
    a column of its own is forced into this one - see DeductionType.save().

    Call this ONCE per view and reuse the result; it is consulted per employee.
    """
    rows = _deduction_type_rows()
    if not rows:
        return list(OTHER_DEDUCTION_CATEGORIES)
    return [c for c, _n, t, _a, _m, rollup, _col in rows
            if t == 'deduction' and rollup]


def deduction_type_meta():
    """[(code, name, entry_type), ...] for every type, active or not.

    Includes inactive types deliberately: a paid snapshot can carry a category
    whose type was deactivated afterwards, and that amount still needs a label.
    """
    rows = _deduction_type_rows() or _fallback_rows()
    return [(c, n, t) for c, n, t, _a, _m, _r, _col in rows]


def deduction_type_colours():
    """{code: hex colour} for badge styling. Empty when unseeded."""
    return {c: (col or '#64748b')
            for c, _n, _t, _a, _m, _r, col in _deduction_type_rows()}


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
        # Table-backed via deduction_codes(), which falls back to the static
        # _DEDUCTION_CATS set when the master table is empty - so an unseeded
        # database behaves exactly as it did before Phase 2.
        return 'deduction' if self.category in deduction_codes() else 'addition'

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


class PayrollNote(models.Model):
    """
    Phase C — free-text, timestamped note attached to an employee, shown in
    the per-employee Notes & Timeline modal on the payroll dashboard
    alongside the auto-generated deduction/payment/carryover history.

    Manually created only (via the "+ Add Note" box); never edited or
    deleted through the UI — an append-only comment log.
    """
    employee = models.ForeignKey(
        'attendance.Employee',
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='payroll_notes',
    )
    remote_employee = models.ForeignKey(
        'attendance.RemoteEmployee',
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='payroll_notes',
    )
    text = models.TextField()
    created_by = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['employee', '-created_at'], name='payroll_pay_employe_a1f2c3_idx'),
            models.Index(fields=['remote_employee', '-created_at'], name='payroll_pay_remote__b4d5e6_idx'),
        ]

    def clean(self):
        super().clean()
        if self.employee and self.remote_employee:
            raise ValidationError("A note must be linked to either an in-house or remote employee, not both.")
        if not self.employee and not self.remote_employee:
            raise ValidationError("A note must be linked to an employee.")

    def __str__(self):
        emp = self.employee or self.remote_employee
        return f"{emp.name} note @ {self.created_at:%Y-%m-%d %H:%M}"


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

    # ---- Phase E6: payment execution -------------------------------------
    # These record HOW the locked figure above was actually settled. They never
    # alter final_salary or snapshot — those remain the immutable computed
    # payroll. amount_paid is what left the business; final_salary is what was
    # owed. The two are equal for a full payment and differ for a partial one.
    amount_paid = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text=(
            "Amount actually disbursed. Equals final_salary for a full payment, "
            "less for a partial one. NULL only on rows created before partial "
            "payments existed — those are treated as paid in full."
        ),
    )
    payment_method = models.CharField(
        max_length=20, choices=PAYMENT_METHOD_CHOICES, blank=True,
        help_text=(
            "How the disbursement was made. 'bank_transfer' is labelled with the "
            "employee's visa provider at display time. Blank on legacy rows."
        ),
    )
    payment_date = models.DateField(
        null=True, blank=True,
        help_text=(
            "Value date of the disbursement. Distinct from paid_at, which is the "
            "timestamp the record was created in the system."
        ),
    )
    payment_splits = models.JSONField(
        null=True, blank=True,
        help_text=(
            "When the disbursement was split across more than one payment method, "
            "the itemized [{method, amount}, ...] breakdown; amounts sum to "
            "amount_paid. NULL when a single method covered the whole payment — "
            "payment_method/amount_paid alone are authoritative in that case."
        ),
    )

    class Meta:
        ordering = ['-year', '-month']

    def __str__(self):
        emp = self.employee or self.remote_employee
        return f"Paid {self.month}/{self.year} — {emp.name if emp else '?'} ({self.final_salary} {self.currency})"

    @property
    def effective_amount_paid(self):
        """What was actually disbursed, resolving legacy NULLs to a full payment.

        Rows created before Phase E6 carry no amount_paid but were only ever
        created by a full "Mark as Paid", so the full computed salary is the
        honest reading — not zero, which would misreport settled historical
        months as unpaid.
        """
        return self.final_salary if self.amount_paid is None else self.amount_paid

    @property
    def is_partial(self):
        """True when less than the full computed salary was disbursed."""
        return self.effective_amount_paid < self.final_salary

    @property
    def balance_due(self):
        """Outstanding amount still owed on this month's salary. Never negative —
        an overpayment is not a negative balance, it is a separate matter."""
        return max(Decimal('0.00'), self.final_salary - self.effective_amount_paid)


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
# ===========================================================================
# Phase 2 - Deduction Master
# ===========================================================================

class DeductionType(models.Model):
    """Configurable master list of deduction and addition types.

    Replaces the hard-coded DEDUCTION_CATEGORY_CHOICES list with rows a user
    can edit, without touching a single stored amount:

    `DeductionEntry.category` is UNCHANGED - same column, same values, same
    max_length. This table is a *registry keyed on those values*, not a foreign
    key. That is deliberate. Converting `category` to an FK would rewrite every
    historical deduction row on a live payroll database in order to gain
    referential integrity the application does not currently need, and would
    make the migration impossible to reverse cleanly. A registry gives the
    configurability the specification asks for at zero risk to history.

    WHAT IS ENFORCED, AND WHAT IS NOT
    ---------------------------------
    Every field on this model changes real behaviour today:

        is_active           - hidden from the entry form; existing entries keep working
        allow_manual_entry  - excluded from the entry form (attendance-derived types)
        allow_split_months  - the entry form refuses split_months > 1
        rolls_up_to_other   - which dashboard column the amount lands in
        requires_note       - the entry form refuses a blank note
        sort_order / colour / name - presentation

    Fields the specification asks for that are NOT here yet - approval
    requirement, document requirement, employee consent, automatic Recoverable
    creation, statutory ceilings and recovery priority - arrive with the phases
    that actually implement them (3, 4, 5 and 6). A checkbox that silently does
    nothing is worse than a missing one: somebody ticks "requires approval",
    believes approval is now required, and it is not.

    `classification`, `description` and `gl_account_code` are documentation
    fields, inert by design. `gl_account_code` is a label held ready for the
    Phase 9 GL export; nothing posts to a ledger today.
    """

    ENTRY_TYPES = [
        ('deduction', 'Deduction'),
        ('addition', 'Addition'),
    ]

    CLASSIFICATIONS = [
        ('statutory', 'Statutory'),
        ('contractual', 'Contractual'),
        ('recovery', 'Recovery / Loan'),
        ('attendance', 'Attendance-derived'),
        ('disciplinary', 'Disciplinary'),
        ('voluntary', 'Voluntary'),
        ('other', 'Other'),
    ]

    code = models.SlugField(
        max_length=30, unique=True,
        help_text='Stable identifier stored on every deduction entry. '
                  'Cannot exceed 30 characters - it must fit '
                  'DeductionEntry.category - and cannot be changed once used.',
    )
    name = models.CharField(max_length=120, help_text='Label shown to users.')
    entry_type = models.CharField(
        max_length=10, choices=ENTRY_TYPES, default='deduction',
        help_text='Whether this reduces or increases net pay. '
                  'Fixed at creation - flipping it would reverse the sign of '
                  'every historical entry already carrying this code.',
    )
    classification = models.CharField(
        max_length=20, choices=CLASSIFICATIONS, default='other',
        help_text='Reporting grouping only. Does not affect calculation.',
    )
    description = models.TextField(blank=True)

    is_active = models.BooleanField(
        default=True,
        help_text='Inactive types disappear from the entry form. Existing '
                  'entries continue to be deducted - deactivating a type is '
                  'not a way to cancel money already scheduled.',
    )
    is_system = models.BooleanField(
        default=False,
        help_text='One of the nine original built-in categories. Payroll '
                  'calculation and dashboard columns refer to these codes '
                  'directly, so they cannot be renamed at code level or '
                  'deleted.',
    )
    allow_manual_entry = models.BooleanField(
        default=True,
        help_text='Off for types payroll derives itself from attendance '
                  '(leave, late). Adding those by hand deducts the same '
                  'absence twice.',
    )
    allow_split_months = models.BooleanField(
        default=True,
        help_text='Whether the amount may be spread over several months.',
    )
    rolls_up_to_other = models.BooleanField(
        default=True,
        help_text='Show this amount in the dashboard "Other" deductions '
                  'column. Only the four types with a dedicated column '
                  '(Late, Leave, Advance, Carryover) may turn this off - '
                  'every other deduction must land in exactly one column or '
                  'the itemized figures stop summing to the total.',
    )
    requires_note = models.BooleanField(
        default=False,
        help_text='Refuse to save an entry of this type without a note.',
    )

    gl_account_code = models.CharField(
        max_length=40, blank=True,
        help_text='General ledger account. Recorded for the future GL export; '
                  'nothing posts to a ledger yet.',
    )
    colour = models.CharField(
        max_length=7, blank=True, default='',
        help_text='Hex colour for the badge, e.g. #eb6834. Blank uses the default grey.',
    )
    sort_order = models.PositiveIntegerField(
        default=100, help_text='Lower numbers appear first in menus.',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.CharField(max_length=150, blank=True)
    updated_by = models.CharField(max_length=150, blank=True)

    class Meta:
        ordering = ['sort_order', 'name']
        verbose_name = 'Deduction Type'
        verbose_name_plural = 'Deduction Types'

    # -- guards ------------------------------------------------------------

    #: Deduction codes that own a dedicated dashboard column and therefore must
    #: NOT also roll up into "Other" - they would be counted twice.
    DEDICATED_COLUMN_CODES = ('late_deduction', 'leave_deduction', 'advance')

    def clean(self):
        super().clean()
        if self.code:
            self.code = self.code.strip().lower()
        if len(self.code or '') > 30:
            raise ValidationError({'code': 'Code cannot exceed 30 characters.'})
        if not re.match(r'^[a-z][a-z0-9_]*$', self.code or ''):
            raise ValidationError({
                'code': 'Code must start with a letter and contain only '
                        'lowercase letters, digits and underscores.'
            })
        if self.pk:
            was = type(self).objects.filter(pk=self.pk).values(
                'code', 'entry_type', 'is_system').first()
            if was:
                if was['is_system'] and self.code != was['code']:
                    raise ValidationError({
                        'code': 'The code of a built-in type cannot be changed - '
                                'payroll calculation refers to it by name.'
                    })
                if self.entry_type != was['entry_type'] and self.has_entries():
                    raise ValidationError({
                        'entry_type': 'This type already has entries. Switching '
                                      'between deduction and addition would '
                                      'reverse the sign of money already recorded.'
                    })
        if self.colour and not re.match(r'^#[0-9a-fA-F]{6}$', self.colour):
            raise ValidationError({'colour': 'Colour must be a hex value like #eb6834.'})

    def has_entries(self):
        return DeductionEntry.objects.filter(category=self.code).exists()

    def entry_count(self):
        return DeductionEntry.objects.filter(category=self.code).count()

    def save(self, *args, **kwargs):
        if self.code:
            self.code = self.code.strip().lower()
        # Additions are not shown in the deductions columns at all, so the
        # rollup flag is meaningless for them - normalise it rather than
        # leaving a value that reads as if it did something.
        if self.entry_type != 'deduction':
            self.rolls_up_to_other = False
        elif self.code not in self.DEDICATED_COLUMN_CODES:
            # A deduction with no column of its own MUST roll into "Other",
            # otherwise its amount is in the Deductions total but in none of
            # the itemized columns, and the row silently stops reconciling.
            self.rolls_up_to_other = True
        super().save(*args, **kwargs)
        invalidate_deduction_type_cache()

    def delete(self, *args, **kwargs):
        if self.is_system:
            raise ValidationError('Built-in types cannot be deleted. Deactivate it instead.')
        if self.has_entries():
            raise ValidationError(
                f'{self.name} has {self.entry_count()} entry(s) recorded against it. '
                'Deactivate it instead - deleting would orphan those amounts.'
            )
        result = super().delete(*args, **kwargs)
        invalidate_deduction_type_cache()
        return result

    @property
    def badge_colour(self):
        return self.colour or ('#b91c1c' if self.entry_type == 'deduction' else '#166534')

    def __str__(self):
        return f'{self.name} ({self.code})'
# ===========================================================================
# Phase 3 - Loans & Salary Advances
# ===========================================================================

class Loan(models.Model):
    """An amount advanced to an employee and recovered from later payroll.

    INTEREST-FREE BY DESIGN. There is no rate, no interest column and no
    amortisation: the schedule is principal divided across N months, and the
    instalments sum to the principal exactly. This was a deliberate decision
    (UAE private-sector employee loans are normally interest-free), and adding
    interest later means a new migration rather than un-rotting dormant fields.

    HOW THIS REACHES PAYROLL - read this before changing anything
    ------------------------------------------------------------
    Activating a loan writes one ordinary `DeductionEntry` per instalment
    (`split_months=1`, category `loan_repayment`), each linked back to its
    `LoanInstallment`. Payroll then deducts it through the code path it already
    uses for every other deduction.

    That is the whole integration. **No payroll calculation code is changed by
    this phase** - which matters, because the Phase 0 regression baseline has
    not been captured yet. A loan changes what is deducted only in the same way
    that adding a deduction by hand does: by creating data, not by altering the
    engine.

    RELATIONSHIP TO `Recoverable`
    -----------------------------
    A loan wraps a `Recoverable` rather than replacing it. `Recoverable` stays
    the single sub-ledger answering "what does this person owe us" - the
    employee profile page already reads it. The loan owns the *schedule*; the
    Recoverable carries the running balance, kept in step by
    `payroll.services_loans.sync_recoverable`.
    """

    STATUS_DRAFT = 'draft'
    STATUS_ACTIVE = 'active'
    STATUS_SETTLED = 'settled'
    STATUS_CANCELLED = 'cancelled'
    STATUS_ON_HOLD = 'on_hold'

    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_ACTIVE, 'Active'),
        (STATUS_SETTLED, 'Settled'),
        (STATUS_CANCELLED, 'Cancelled'),
        (STATUS_ON_HOLD, 'On Hold'),
    ]

    #: Mirrors Recoverable.RECOVERABLE_TYPES so the wrapped ledger row can be
    #: created with a matching type without a translation table.
    PURPOSE_CHOICES = [
        ('advance', 'Salary Advance'),
        ('visa_cost', 'Visa Cost'),
        ('asset', 'Asset / Equipment'),
        ('training', 'Training Cost'),
        ('air_ticket', 'Air Ticket'),
        ('relocation', 'Relocation Cost'),
        ('other', 'Other'),
    ]

    #: Deduction Master code every loan instalment is posted under. Seeded by
    #: migration 0030. Kept distinct from 'advance' so a loan instalment is
    #: never confused with an ad-hoc advance typed in by hand, and so loans can
    #: be reported on separately.
    DEDUCTION_CODE = 'loan_repayment'

    reference = models.CharField(
        max_length=24, unique=True,
        help_text='Human reference, e.g. LN-2026-0007. Generated on save when blank.',
    )
    employee = models.ForeignKey(
        'attendance.Employee', on_delete=models.CASCADE,
        null=True, blank=True, related_name='loans',
    )
    remote_employee = models.ForeignKey(
        'attendance.RemoteEmployee', on_delete=models.CASCADE,
        null=True, blank=True, related_name='loans',
    )
    recoverable = models.OneToOneField(
        'attendance.Recoverable', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='loan',
        help_text='The sub-ledger row this loan keeps in step. Created with the loan.',
    )

    purpose = models.CharField(max_length=30, choices=PURPOSE_CHOICES, default='advance')
    description = models.CharField(max_length=255)
    principal = models.DecimalField(
        max_digits=12, decimal_places=2,
        help_text='Total amount advanced. Instalments always sum to exactly this.',
    )
    currency = models.CharField(
        max_length=3, default='AED',
        help_text="Set from the employee's currency when the loan is created.",
    )
    installment_count = models.PositiveIntegerField(
        default=1, help_text='Number of monthly instalments (1 = recovered in full next month).',
    )
    first_deduction_year = models.IntegerField()
    first_deduction_month = models.IntegerField(help_text='1-12')

    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT, db_index=True,
        help_text='Draft loans deduct nothing. Activating writes the deduction entries.',
    )
    note = models.TextField(blank=True)

    created_by = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    activated_by = models.CharField(max_length=150, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.CharField(max_length=150, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['employee', 'status']),
            models.Index(fields=['remote_employee', 'status']),
        ]

    # -- identity ----------------------------------------------------------

    @property
    def person(self):
        return self.employee or self.remote_employee

    @property
    def employee_type(self):
        return 'inhouse' if self.employee_id else 'remote'

    # -- money -------------------------------------------------------------

    @property
    def total_scheduled(self):
        """Sum of every instalment. Equals `principal` for a saved schedule."""
        return sum((i.due_amount for i in self.installments.all()), Decimal('0.00'))

    @property
    def total_recovered(self):
        return sum((i.amount_recovered for i in self.installments.all()), Decimal('0.00'))

    @property
    def total_waived(self):
        return sum(
            (i.due_amount for i in self.installments.all()
             if i.status == LoanInstallment.STATUS_WAIVED),
            Decimal('0.00'),
        )

    @property
    def outstanding(self):
        """What is still owed: principal less what was recovered or forgiven."""
        return max(Decimal('0.00'),
                   self.principal - self.total_recovered - self.total_waived)

    @property
    def is_closed(self):
        return self.status in (self.STATUS_SETTLED, self.STATUS_CANCELLED)

    # -- validation --------------------------------------------------------

    def clean(self):
        super().clean()
        if self.employee and self.remote_employee:
            raise ValidationError(
                'A loan must be linked to either an in-house or remote employee, not both.')
        if not self.employee and not self.remote_employee:
            raise ValidationError('A loan must be linked to an employee.')
        if self.principal is None or self.principal <= 0:
            raise ValidationError({'principal': 'Principal must be greater than zero.'})
        if self.installment_count < 1:
            raise ValidationError({'installment_count': 'There must be at least one instalment.'})
        if not 1 <= (self.first_deduction_month or 0) <= 12:
            raise ValidationError({'first_deduction_month': 'Month must be between 1 and 12.'})
        # A per-instalment amount that rounds to zero means the schedule cannot
        # represent the principal at all - reject it rather than write a
        # schedule of 0.00 rows that silently never recovers anything.
        if self.principal and self.installment_count:
            if (self.principal / self.installment_count) < Decimal('0.01'):
                raise ValidationError({
                    'installment_count':
                        f'{self.installment_count} instalments of '
                        f'{self.principal} would be less than 0.01 each.'
                })

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = self._next_reference()
        super().save(*args, **kwargs)

    def _next_reference(self):
        """LN-<year>-<sequence>, unique. Falls back to the pk-free random suffix
        only if the sequence is somehow taken, so a save never fails on this."""
        year = self.first_deduction_year or 0
        prefix = f'LN-{year}-'
        last = (type(self).objects.filter(reference__startswith=prefix)
                .order_by('-reference').values_list('reference', flat=True).first())
        seq = 1
        if last:
            try:
                seq = int(last.rsplit('-', 1)[1]) + 1
            except (IndexError, ValueError):
                seq = type(self).objects.filter(reference__startswith=prefix).count() + 1
        for attempt in range(seq, seq + 50):
            candidate = f'{prefix}{attempt:04d}'
            if not type(self).objects.filter(reference=candidate).exists():
                return candidate
        return f'{prefix}{seq:04d}-{id(self) % 1000:03d}'

    def __str__(self):
        who = self.person.name if self.person else '?'
        return f'{self.reference} - {who}: {self.currency} {self.principal}'


class LoanInstallment(models.Model):
    """One scheduled monthly repayment of a Loan.

    The schedule is the authoritative record of what is owed when. Amounts are
    generated so they sum to the principal EXACTLY - see
    `payroll.services_loans.build_schedule` for why that needs care.

    `deduction_entry` is the bridge into payroll: when the loan is activated
    each instalment gets its own `DeductionEntry`, and that is what the payroll
    calculation actually sees. If the entry is deleted from the deductions
    screen, this row's link goes null and the instalment reverts to scheduled -
    it is not silently treated as recovered.
    """

    STATUS_SCHEDULED = 'scheduled'
    STATUS_POSTED = 'posted'
    STATUS_RECOVERED = 'recovered'
    STATUS_WAIVED = 'waived'
    STATUS_SKIPPED = 'skipped'

    STATUS_CHOICES = [
        (STATUS_SCHEDULED, 'Scheduled'),
        (STATUS_POSTED, 'Posted to payroll'),
        (STATUS_RECOVERED, 'Recovered'),
        (STATUS_WAIVED, 'Waived'),
        (STATUS_SKIPPED, 'Skipped'),
    ]

    loan = models.ForeignKey(Loan, on_delete=models.CASCADE, related_name='installments')
    sequence = models.PositiveIntegerField(help_text='1-based position in the schedule.')
    year = models.IntegerField()
    month = models.IntegerField(help_text='1-12')
    due_amount = models.DecimalField(max_digits=12, decimal_places=2)
    amount_recovered = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_SCHEDULED, db_index=True)
    deduction_entry = models.ForeignKey(
        'payroll.DeductionEntry', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='loan_installments',
        help_text='The payroll deduction this instalment created, if posted.',
    )
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['loan_id', 'sequence']
        constraints = [
            models.UniqueConstraint(fields=['loan', 'sequence'], name='uniq_loan_sequence'),
            models.UniqueConstraint(fields=['loan', 'year', 'month'], name='uniq_loan_period'),
        ]
        indexes = [models.Index(fields=['year', 'month', 'status'])]

    @property
    def period_index(self):
        return self.year * 12 + (self.month - 1)

    @property
    def outstanding(self):
        if self.status in (self.STATUS_WAIVED, self.STATUS_SKIPPED):
            return Decimal('0.00')
        return max(Decimal('0.00'), self.due_amount - self.amount_recovered)

    def clean(self):
        super().clean()
        if not 1 <= (self.month or 0) <= 12:
            raise ValidationError({'month': 'Month must be between 1 and 12.'})
        if self.due_amount is not None and self.due_amount < 0:
            raise ValidationError({'due_amount': 'An instalment cannot be negative.'})

    def __str__(self):
        return f'{self.loan.reference} #{self.sequence} {self.year}/{self.month:02d}: {self.due_amount}'

