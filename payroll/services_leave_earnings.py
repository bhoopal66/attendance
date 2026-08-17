"""
Annual leave earnings — what leave is worth, taken or encashed.

TWO FIGURES, TWO BASES
----------------------
Federal Decree-Law 33/2021, Article 29:

    leave TAKEN     paid at the FULL WAGE — basic plus every regular allowance
    leave ENCASHED  paid on BASIC SALARY only, unless the contract is better

They are not the same number and must never be computed from one rate. Using
the full wage for encashment overpays; using basic for leave taken is the
non-compliance the article exists to prevent.

WHY BOTH LEAVE TABLES ARE READ
------------------------------
Annual leave can be recorded in `AnnualLeave` (admin-assigned, with a paid
percentage) OR as a `LeaveRequest` with leave_type='annual'. Both exist, both
are used, and the same absence can appear in both. Counting the tables
separately double-counts those days and inflates the balance consumed, so this
module resolves both to a SET OF DATES and counts the union. A day is a day
however it was recorded.

WHAT IT REFUSES TO DO
---------------------
Encashment needs the basic component. `RemoteEmployee` has no SalaryStructure
at all and some in-house staff have none approved, so basic is genuinely
unknown for them. This returns None with a reason rather than splitting the
gross by some assumed ratio: a fabricated basic becomes a real number on a
final settlement, and nobody downstream would know it was invented.
"""

import calendar
import datetime
import logging
from decimal import Decimal, ROUND_HALF_UP

logger = logging.getLogger('payroll')

CENT = Decimal('0.01')

# Statutory minimums. Contracts may be more generous — `accrued_days` takes a
# policy so a better entitlement is passed in rather than edited in here.
ACCRUAL_MIN_MONTHS = 6
ACCRUAL_SHORT_DAYS_PER_MONTH = 2.0
ACCRUAL_FULL_DAYS_PER_YEAR = 30.0

# The UAE leave-salary convention. NOTE: payroll/views.py divides by the days
# in the pay period (28-31) instead, which keeps a day paid equal to a day
# deducted. Both are defensible; they differ by ~3% in a 31-day month. The
# divisor is therefore an argument everywhere below, never a hidden constant.
DEFAULT_DIVISOR = 30


def _d(value):
    return Decimal(str(value or 0))


def months_of_service(joining_date, as_of):
    """Whole months plus the fraction of the current one, or None."""
    if not joining_date or not as_of or as_of < joining_date:
        return None
    months = (as_of.year - joining_date.year) * 12 + (as_of.month - joining_date.month)
    # The anniversary day has to be CLAMPED to the length of the month being
    # measured into, not compared raw. Someone who joined on 31 January has
    # completed a month on 29 February — there is no 31 February, so the month
    # end is the anniversary. Comparing 29 < 31 silently took a month away from
    # every month-end joiner, and a month of service is a day of leave.
    anniversary_day = min(joining_date.day,
                          calendar.monthrange(as_of.year, as_of.month)[1])
    if as_of.day < anniversary_day:
        months -= 1
    if months < 0:
        return 0.0
    # calendar.monthrange, not a hand-rolled month-length table. The first
    # version of this carried its own list of month lengths with a `% 4 == 0`
    # leap-year test — wrong for 1900 and 2100, and unverifiable at a glance.
    # Leave accrual turns into money; it does not get bespoke calendar maths.
    total = joining_date.month - 1 + months
    year = joining_date.year + total // 12
    month = total % 12 + 1
    anchor = datetime.date(year, month,
                           min(joining_date.day, calendar.monthrange(year, month)[1]))
    # The remainder is expressed in 30ths, matching the ÷30 leave convention
    # used throughout this module rather than the length of whichever month the
    # anniversary happens to land in.
    return months + max(0.0, (as_of - anchor).days / 30.0)


def accrued_days(joining_date, as_of, days_per_year=ACCRUAL_FULL_DAYS_PER_YEAR):
    """Statutory entitlement accrued by `as_of`.

    Under six months earns nothing — that is a rule, not a rounding, so it
    returns exactly 0 rather than a small pro-rata figure that would look like
    an entitlement somebody could ask for.
    """
    months = months_of_service(joining_date, as_of)
    if months is None:
        return None
    if months < ACCRUAL_MIN_MONTHS:
        return 0.0
    if months < 12:
        return round(months * ACCRUAL_SHORT_DAYS_PER_MONTH, 1)
    return round(months / 12.0 * days_per_year, 1)


def annual_leave_dates(employee, until=None, since=None):
    """Every calendar date this employee was on annual leave, de-duplicated.

    A set, deliberately. The same absence recorded in both leave tables
    contributes one date, not two.
    """
    from attendance.models import AnnualLeave, LeaveRequest

    until = until or datetime.date.today()
    dates = set()

    kind = 'employee' if employee.__class__.__name__ == 'Employee' else 'remote_employee'
    qs = AnnualLeave.objects.filter(**{kind: employee, 'start_date__lte': until})
    if since:
        qs = qs.filter(end_date__gte=since)
    for al in qs:
        day, last = al.start_date, min(al.end_date, until)
        while day <= last:
            if not since or day >= since:
                dates.add(day)
            day += datetime.timedelta(days=1)

    # LeaveRequest is in-house only — the model has no remote FK. A remote
    # employee's annual leave can therefore only ever arrive as an AnnualLeave
    # row. That asymmetry belongs to the models, not to this report, and it is
    # surfaced by `leave_summary` rather than hidden.
    if kind == 'employee':
        lr = LeaveRequest.objects.filter(employee=employee, status='approved',
                                         leave_type='annual', start_date__lte=until)
        if since:
            lr = lr.filter(end_date__gte=since)
        for req in lr:
            day, last = req.start_date, min(req.end_date, until)
            while day <= last:
                if not since or day >= since:
                    dates.add(day)
                day += datetime.timedelta(days=1)
    return dates


def wage_components(employee, as_of=None):
    """(full_wage, basic, source) — basic is None when genuinely unknown."""
    from payroll.views import get_effective_salary_structure

    as_of = as_of or datetime.date.today()
    if employee.__class__.__name__ == 'Employee':
        structure = get_effective_salary_structure(employee, as_of)
        if structure is not None:
            basic = _d(structure.basic)
            full = (basic + _d(structure.housing) + _d(structure.transport)
                    + _d(structure.phone) + _d(structure.other_allowance))
            return full.quantize(CENT), basic.quantize(CENT), 'salary structure'
    # No approved structure, or a remote employee, which has none by design.
    return _d(employee.salary).quantize(CENT), None, 'Employee.salary (no structure)'


def daily_rate(amount, divisor=DEFAULT_DIVISOR):
    """None, not zero, when it cannot be worked out.

    Zero would read as "a day here is worth nothing", which is a claim. Unknown
    is the truth.
    """
    if amount is None or not divisor:
        return None
    return (Decimal(amount) / Decimal(divisor)).quantize(CENT, rounding=ROUND_HALF_UP)


def leave_pay(days, full_wage, divisor=DEFAULT_DIVISOR):
    """Leave TAKEN — full wage. Article 29."""
    rate = daily_rate(full_wage, divisor)
    if rate is None:
        return None
    return (rate * _d(days)).quantize(CENT, rounding=ROUND_HALF_UP)


def encashment(days, basic, divisor=DEFAULT_DIVISOR):
    """Leave ENCASHED — basic only. None when basic is unknown."""
    rate = daily_rate(basic, divisor)
    if rate is None:
        return None
    return (rate * _d(max(0.0, days))).quantize(CENT, rounding=ROUND_HALF_UP)


def leave_summary(employee, as_of=None, divisor=DEFAULT_DIVISOR,
                  days_per_year=ACCRUAL_FULL_DAYS_PER_YEAR):
    """Everything about one employee's annual leave standing."""
    as_of = as_of or datetime.date.today()
    joining = getattr(employee, 'joining_date', None)

    accrued = accrued_days(joining, as_of, days_per_year)
    taken = float(len(annual_leave_dates(employee, until=as_of)))
    balance = None if accrued is None else round(accrued - taken, 1)

    full, basic, source = wage_components(employee, as_of)
    day_full = daily_rate(full, divisor)
    day_basic = daily_rate(basic, divisor)

    notes = []
    if joining is None:
        notes.append('no joining date — entitlement cannot be accrued')
    if basic is None:
        notes.append('basic salary unknown (%s) — encashment not computed rather '
                     'than estimated' % source)
    if balance is not None and balance < 0:
        notes.append('%.1f days taken beyond entitlement — leave in advance, '
                     'recoverable only if agreed in writing' % abs(balance))
    if employee.__class__.__name__ != 'Employee':
        notes.append('remote employee — LeaveRequest cannot reach them, so only '
                     'AnnualLeave rows were counted')

    return {
        'employee': employee,
        'employee_type': 'inhouse' if employee.__class__.__name__ == 'Employee' else 'remote',
        'name': employee.name,
        'tcr': getattr(employee, 'tcr_id', '') or '',
        'currency': getattr(employee, 'currency', 'AED') or 'AED',
        'joining_date': joining,
        'months_service': months_of_service(joining, as_of),
        'accrued_days': accrued,
        'taken_days': taken,
        'balance_days': balance,
        'full_wage': full,
        'basic': basic,
        'wage_source': source,
        'divisor': divisor,
        'day_rate_full': day_full,
        'day_rate_basic': day_basic,
        'encashment_value': (None if (balance is None or basic is None)
                             else encashment(balance, basic, divisor)),
        'notes': notes,
    }
