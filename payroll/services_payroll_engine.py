"""
Phase 1 — payroll calculation seam.

WHAT THIS IS, PRECISELY
-----------------------
This module defines the *public contract* for "calculate an employee's payroll".
It does not yet contain the calculation. Every function here delegates to the
existing implementations in `payroll/views.py`, which remain the single source
of truth for the maths.

That is deliberate, and it is the whole point of this phase.

WHY A SEAM RATHER THAN MOVING THE CODE
--------------------------------------
`payroll/views.py` is ~275 KB and holds calculation, rendering, exports and
endpoints together. Lifting the calculation out in one move would be a large,
unreviewable diff against live payroll — and the regression baseline
(`manage.py payroll_snapshot --write`) may not have been captured yet. A
refactor you cannot verify is not a refactor; it is a rewrite with optimism.

So this phase changes no behaviour at all. It creates the boundary. Once the
baseline exists, implementations move behind this boundary **one function at a
time**, and after each move:

    python manage.py payroll_snapshot --check --year 2026 --month 7

must print PASS. If it does not, that move is wrong and gets reverted. Callers
never notice, because they were already calling this module rather than the
monolith's private helpers.

WHAT CALLERS SHOULD DO
----------------------
New code — the deductions/loans module in particular — should import from here
and never from `payroll.views`:

    from payroll.services_payroll_engine import (
        get_pay_period, calculate_employee_payroll, build_all_sections,
    )

Importing `payroll.views._get_sales_payroll_row` directly coupled a caller to a
private function inside a 275 KB view module; every such call site is another
thing that must move in lockstep later. This module exists so there is exactly
one thing to move.

DUAL-EMPLOYEE MODEL — READ THIS
-------------------------------
`Employee` (in-house) and `RemoteEmployee` are separate tables sharing
`BaseEmployee`. Which calculation an employee gets depends on BOTH their type
and their department, and the routing is not obvious:

    in-house + department='Admin'  -> _get_inhouse_payroll_row   (attendance-scaled)
    in-house + anything else       -> _get_sales_payroll_row     (commission path)
    remote   + any department      -> _get_sales_payroll_row     (commission path)

That routing is mirrored from `mark_paid_salary`, which is the authoritative
version — it is what actually gets locked into a payslip snapshot.

Naming note: flat `services_*.py`, matching `services_management.py`,
`services_performance.py` and `services_profitability.py`. A `payroll/services/`
package is NOT possible here — `payroll/services.py` already exists as a module
and the two would be ambiguous to the importer.
"""

from collections import OrderedDict, namedtuple


#: A resolved pay period. `days` and `holidays` are what the calculation needs;
#: both are derived from the employee's own `salary_cycle_start_day`, so two
#: employees in the same calendar month can legitimately have different periods.
PayPeriod = namedtuple('PayPeriod', ['start', 'end', 'days', 'holidays'])

#: Section keys, in dashboard order. Stable identifiers — the deductions module
#: and the regression harness both key off these, so do not rename casually.
SECTION_ADMIN_INHOUSE = 'admin_inhouse'
SECTION_ADMIN_REMOTE = 'admin_remote'
SECTION_SALES_FIXED = 'sales_fixed'
SECTION_SALES_PERF = 'sales_perf'
SECTION_SALES_PERF_METHOD2 = 'sales_perf_method2'

SECTIONS = (
    SECTION_ADMIN_INHOUSE,
    SECTION_ADMIN_REMOTE,
    SECTION_SALES_FIXED,
    SECTION_SALES_PERF,
    SECTION_SALES_PERF_METHOD2,
)


# ---------------------------------------------------------------- pay period

def get_pay_period(employee, year, month, _cache=None):
    """Resolve one employee's pay period for a given calendar month.

    `_cache` is an optional dict for callers processing many employees — the
    period depends only on `salary_cycle_start_day`, so a handful of distinct
    values covers the whole workforce and recomputing per employee is waste.
    """
    from payroll.views import _get_employee_pay_period

    day = employee.salary_cycle_start_day or 21
    if _cache is not None and day in _cache:
        return _cache[day]
    start, end, days, holidays = _get_employee_pay_period(day, year, month)
    period = PayPeriod(start, end, days, holidays)
    if _cache is not None:
        _cache[day] = period
    return period


# ------------------------------------------------------------- per employee

def calculate_employee_payroll(employee, employee_type, year, month,
                               banks=None, period=None):
    """Compute one employee's payroll row for one month.

    Returns the row dict the dashboard and payslip already use — earnings,
    attendance, deductions, commission and net — with the Gross Pay salary
    components attached.

    `employee_type` must be 'inhouse' or 'remote'; it cannot be inferred
    reliably from the instance alone, because a person can exist as both an
    `Employee` and a `RemoteEmployee` linked by `tcr_id`, and the caller's
    context decides which record is being paid.

    Pass `banks` when calling in a loop; otherwise it is queried per call.
    """
    from payroll.models import Bank
    from payroll.views import (
        _attach_gross_breakdown,
        _get_inhouse_payroll_row,
        _get_sales_payroll_row,
    )

    if employee_type not in ('inhouse', 'remote'):
        raise ValueError(f"employee_type must be 'inhouse' or 'remote', got {employee_type!r}")

    if period is None:
        period = get_pay_period(employee, year, month)
    if banks is None:
        banks = list(Bank.objects.filter(is_active=True).order_by('name'))

    if employee_type == 'inhouse' and employee.department == 'Admin':
        row = _get_inhouse_payroll_row(
            employee, year, month, period.start, period.end, period.holidays,
            days_in_period=period.days,
        )
        # _get_inhouse_payroll_row already returns the salary components.
        return row

    row = _get_sales_payroll_row(
        employee, year, month, employee_type, banks,
        period.days, period.holidays,
        period_start=period.start, period_end=period.end,
    )
    _attach_gross_breakdown(row, period.end)
    return row


def calculate_method2_row(employee, year, month, period=None):
    """The experimental talktime-proportional calculation for remote Sales.

    Kept as its own entry point rather than folded into
    `calculate_employee_payroll`, because it is a *comparison* figure shown
    beside the live one — not an alternative way of paying the same person.
    """
    from payroll.views import _attach_gross_breakdown, _get_sales_performance_test_row

    if period is None:
        period = get_pay_period(employee, year, month)
    row = _get_sales_performance_test_row(
        employee, period.start, period.end, period.days, period.holidays,
        year=year, month=month,
    )
    _attach_gross_breakdown(row, period.end)
    return row


# ------------------------------------------------------- employee selection

def select_employees(year=None, month=None):
    """Who appears in which payroll section.

    Returned as an OrderedDict of section key -> list of (employee, type).

    The `tcr_id` exclusion matters: a person who exists as BOTH an active
    in-house `Employee` and a `RemoteEmployee` is paid once, through their
    in-house record. Dropping this rule double-pays them.
    """
    from attendance.models import Employee, RemoteEmployee

    inhouse_tcr = set(
        Employee.objects.filter(is_active=True)
        .exclude(tcr_id__isnull=True).exclude(tcr_id='')
        .values_list('tcr_id', flat=True)
    )

    def _remote(qs):
        return qs.exclude(tcr_id__in=inhouse_tcr) if inhouse_tcr else qs

    out = OrderedDict((s, []) for s in SECTIONS)

    out[SECTION_ADMIN_INHOUSE] = [
        (e, 'inhouse') for e in
        Employee.objects.filter(department='Admin', is_active=True).order_by('name')
    ]
    out[SECTION_ADMIN_REMOTE] = [
        (e, 'remote') for e in
        _remote(RemoteEmployee.objects.filter(department='Admin', is_active=True)).order_by('name')
    ]
    out[SECTION_SALES_FIXED] = [
        (e, 'inhouse') for e in
        Employee.objects.filter(department='Sales', is_active=True, is_fixed_salary=True).order_by('name')
    ] + [
        (e, 'remote') for e in
        _remote(RemoteEmployee.objects.filter(is_active=True, is_fixed_salary=True)
                .exclude(department='Admin')).order_by('name')
    ]
    perf_inhouse = [
        (e, 'inhouse') for e in
        Employee.objects.filter(department='Sales', is_active=True, is_fixed_salary=False).order_by('name')
    ]
    perf_remote = [
        (e, 'remote') for e in
        _remote(RemoteEmployee.objects.filter(is_active=True, is_fixed_salary=False)
                .exclude(department='Admin')).order_by('name')
    ]
    out[SECTION_SALES_PERF] = perf_inhouse + perf_remote
    # Method 2 is remote-only by definition — it is driven by call talktime.
    out[SECTION_SALES_PERF_METHOD2] = list(perf_remote)
    return out


# --------------------------------------------------------------- whole month

def build_all_sections(year, month):
    """Every payroll row for a month, grouped by section.

    This is the single definition of "what the payroll for this month is".
    The regression harness consumes it, so harness and engine cannot drift
    apart as implementations move behind this seam.
    """
    from payroll.models import Bank

    banks = list(Bank.objects.filter(is_active=True).order_by('name'))
    cache = {}
    selection = select_employees(year, month)
    out = OrderedDict()

    for section, members in selection.items():
        rows = []
        for employee, emp_type in members:
            period = get_pay_period(employee, year, month, _cache=cache)
            if section == SECTION_SALES_PERF_METHOD2:
                rows.append(calculate_method2_row(employee, year, month, period=period))
            else:
                rows.append(calculate_employee_payroll(
                    employee, emp_type, year, month, banks=banks, period=period,
                ))
        out[section] = rows
    return out
