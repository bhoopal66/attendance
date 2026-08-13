"""
Cross-cutting payroll helpers that need to run outside payroll/views.py
(e.g. from attendance/views/employee_management.py when an employee's
currency is changed there).
"""
import datetime
from decimal import Decimal, ROUND_HALF_UP

from .models import DeductionEntry, DeductionCarryover, ExchangeRate


def get_effective_salary_structure(employee, as_of_date):
    """Return the SalaryStructure row effective for `employee` as of `as_of_date`,
    or None if the employee has no approved salary structure covering that date.

    Used by the payroll dashboard and the payslip generator (both in the
    payroll/views.py monolith) so the real Basic/Housing/Transport/Phone/Other
    breakdown entered via the employee's Salary tab (Phase 5) is shown instead
    of a synthetic percentage split of the flat Employee.salary gross figure.

    `employee` must be an attendance.models.Employee instance (in-house only —
    RemoteEmployee has no salary_structures relation; callers should skip this
    lookup for remote employees and keep the existing flat-salary behaviour).
    """
    return (
        employee.salary_structures
        .filter(status='approved', effective_from__lte=as_of_date)
        .order_by('-effective_from', '-created_at')
        .first()
    )


def _rate_for(currency, year, month):
    """Best-effort exchange rate for `currency` in a given year/month.
    Falls back to the nearest month with a stored rate if the exact one is
    missing, so old entries from months without a rate can still convert."""
    if currency == 'AED':
        return Decimal('1')
    rate_obj = ExchangeRate.objects.filter(currency=currency, year=year, month=month).first()
    if rate_obj:
        return rate_obj.rate
    candidates = list(ExchangeRate.objects.filter(currency=currency))
    if not candidates:
        return None
    target_idx = year * 12 + month
    nearest = min(candidates, key=lambda r: abs((r.year * 12 + r.month) - target_idx))
    return nearest.rate


def convert_amount(amount, from_currency, to_currency, year, month):
    """Convert `amount` (in from_currency) to to_currency using the rate(s)
    for the given year/month, going via AED. Returns None if a required rate
    is missing (caller should leave the amount untouched in that case)."""
    if from_currency == to_currency or not amount:
        return amount
    from_rate = _rate_for(from_currency, year, month)
    to_rate = _rate_for(to_currency, year, month)
    if not from_rate or not to_rate:
        return None
    aed_value = amount / from_rate
    return (aed_value * to_rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def convert_employee_deduction_currency(emp_type, employee_id, old_currency, new_currency):
    """Rewrite an employee's still-outstanding DeductionEntry/DeductionCarryover
    raw amounts from old_currency to new_currency, converted at the rate for
    each row's own month. Call this whenever an employee's currency field
    changes.

    Without this, DeductionEntry/DeductionCarryover store a bare number with
    no currency of their own — every view displays it using the employee's
    *current* currency, so switching an employee's currency silently
    reinterprets old amounts (e.g. an AED 1000 advance starts showing as
    INR 1000) instead of converting them.

    Entries that have already been fully recovered are left untouched: they
    were actually paid back in the old currency, so they should keep showing
    the amount that was really recovered, not a value re-cast into the new
    currency after the fact.
    """
    if old_currency == new_currency:
        return
    from .views import _build_deduction_recovery_map, _entry_is_recovered  # avoid import cycle

    filter_kwargs = (
        {'employee_id': employee_id} if emp_type == 'inhouse' else {'remote_employee_id': employee_id}
    )
    key = (emp_type, employee_id)
    today = datetime.date.today()
    today_idx = today.year * 12 + (today.month - 1)
    recovery_map = _build_deduction_recovery_map()

    for entry in DeductionEntry.objects.filter(**filter_kwargs):
        end_year, end_month = entry.end_month_year()
        end_idx = end_year * 12 + (end_month - 1)
        if _entry_is_recovered(end_idx, key, recovery_map, today_idx):
            continue
        converted = convert_amount(entry.total_amount, old_currency, new_currency, entry.start_year, entry.start_month)
        if converted is not None:
            entry.total_amount = converted
            entry.save(update_fields=['total_amount'])

    for co in DeductionCarryover.objects.filter(**filter_kwargs):
        if co.applied_amount >= co.overflow_amount:
            continue  # already fully recovered in the old currency
        new_overflow = convert_amount(co.overflow_amount, old_currency, new_currency, co.from_year, co.from_month)
        new_applied = convert_amount(co.applied_amount, old_currency, new_currency, co.from_year, co.from_month)
        if new_overflow is not None and new_applied is not None:
            co.overflow_amount = new_overflow
            co.applied_amount = new_applied
            co.save(update_fields=['overflow_amount', 'applied_amount'])
