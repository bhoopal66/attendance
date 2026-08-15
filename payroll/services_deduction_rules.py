"""
Phase 4 - deduction rules & limits: evaluation.

WHAT THIS DOES AND DOES NOT DECIDE
----------------------------------
It answers one question: "given the deductions recorded against this employee
for this month, does any active rule bind?" It does not cap anything, does not
reorder recovery, and does not touch the payroll calculation. Enforcement
happens at the point of entry - creating a deduction or activating a loan -
where a person is present to be told why.

Capping *during* calculation is the Phase 5 priority engine, and needs the
Phase 0 regression baseline first.

THREE OUTCOMES, NOT TWO
-----------------------
Every rule evaluates to pass, breach, or **unevaluated**. Unevaluated is not a
pass. A rule keyed to basic salary cannot be checked for a remote employee (no
SalaryStructure exists), and a rule with an amount ceiling in AED is not
applied to an employee paid in INR at an assumed rate. Both are reported
honestly rather than being silently waved through, because a limits engine that
reports "no violations" when it could not look is worse than no engine.
"""

import logging
from collections import namedtuple
from decimal import Decimal

logger = logging.getLogger('payroll')

ZERO = Decimal('0.00')
CENT = Decimal('0.01')

#: One rule's verdict for one employee.
#: outcome: 'pass' | 'breach' | 'unevaluated'
RuleResult = namedtuple('RuleResult', [
    'rule', 'outcome', 'reason', 'basis_amount', 'ceiling', 'applied', 'excess',
])


# ------------------------------------------------------------------- basis

def basis_amount(employee, employee_type, basis, as_of_date):
    """The contractual figure a percentage ceiling is measured against.

    Returns (Decimal, None) on success or (None, reason) when it cannot be
    resolved. The caller must treat a None as *unevaluated*, never as zero -
    zero would make every percentage ceiling trivially breached.
    """
    from .models import DeductionRule

    if employee_type == 'inhouse':
        from .services import get_effective_salary_structure
        st = get_effective_salary_structure(employee, as_of_date)
        if st is None:
            return None, 'no approved salary structure on file for this date'
        if basis == DeductionRule.BASIS_BASIC:
            return Decimal(st.basic), None
        gross = (Decimal(st.basic) + Decimal(st.housing) + Decimal(st.transport)
                 + Decimal(st.phone) + Decimal(st.other_allowance))
        return gross, None

    # Remote employees have no SalaryStructure - only a flat salary figure,
    # which is a gross. There is no basis on which to split out a "basic",
    # and guessing one (40%, say) would invent the very number the rule is
    # meant to constrain.
    if basis == DeductionRule.BASIS_BASIC:
        return None, 'remote employees have no salary structure, so basic pay is unknown'
    salary = getattr(employee, 'salary', None)
    if not salary:
        return None, 'no salary recorded for this employee'
    return Decimal(salary), None


# ------------------------------------------------------------------- rules

def active_rules(year, month):
    """Rules switched on and effective in the given month."""
    from .models import DeductionRule
    return [r for r in DeductionRule.objects.filter(is_active=True)
            if r.is_effective_in(year, month)]


def rule_targets(rule, employee, employee_type):
    """Whether this rule covers this employee at all."""
    from .models import DeductionRule
    if rule.applies_to == DeductionRule.APPLIES_INHOUSE and employee_type != 'inhouse':
        return False
    if rule.applies_to == DeductionRule.APPLIES_REMOTE and employee_type != 'remote':
        return False
    if rule.department:
        dept = (getattr(employee, 'department', '') or '').strip().lower()
        if dept != rule.department.strip().lower():
            return False
    return True


def _covered_codes(rule):
    """Which deduction codes count toward this rule's total. None = all of them."""
    from .models import DeductionRule, Loan
    if rule.scope == DeductionRule.SCOPE_TYPE:
        return {rule.deduction_code}
    if rule.scope == DeductionRule.SCOPE_LOANS:
        return {Loan.DEDUCTION_CODE}
    return None


# --------------------------------------------------------------- deductions

def recorded_deductions(employee, employee_type, year, month, extra=None):
    """{code: Decimal} of deduction entries active for this employee this month.

    Additions are excluded - a rule limits what is taken, not what is given.
    Carryover is included under the pseudo-code 'carryover_in', because it is
    money genuinely coming out of the employee's pay this month.

    `extra` is an optional {code: Decimal} of a *proposed* entry, so the same
    function serves both "check this month" and "would this new entry breach".
    """
    from .models import DeductionCarryover, DeductionEntry, deduction_codes

    kw = ({'employee': employee, 'remote_employee__isnull': True}
          if employee_type == 'inhouse'
          else {'remote_employee': employee, 'employee__isnull': True})

    deduction_only = deduction_codes()
    totals = {}
    for entry in DeductionEntry.objects.filter(**kw):
        if entry.category not in deduction_only:
            continue
        if not entry.is_active_in(year, month):
            continue
        totals[entry.category] = totals.get(entry.category, ZERO) + entry.installment_amount

    co_kw = ({'employee': employee} if employee_type == 'inhouse'
             else {'remote_employee': employee})
    co = (DeductionCarryover.objects
          .filter(to_year=year, to_month=month, **co_kw)
          .exclude(is_skipped=True).first())
    if co:
        totals['carryover_in'] = totals.get('carryover_in', ZERO) + Decimal(co.overflow_amount)

    for code, amount in (extra or {}).items():
        totals[code] = totals.get(code, ZERO) + Decimal(amount)
    return totals


# ------------------------------------------------------------- evaluation

def evaluate(employee, employee_type, year, month, as_of_date=None,
             extra=None, rules=None):
    """Every applicable rule's verdict for one employee in one month."""
    import calendar
    import datetime

    from .models import DeductionRule

    if as_of_date is None:
        as_of_date = datetime.date(year, month, calendar.monthrange(year, month)[1])
    rules = active_rules(year, month) if rules is None else rules
    totals = recorded_deductions(employee, employee_type, year, month, extra=extra)

    results = []
    basis_cache = {}
    for rule in rules:
        if not rule_targets(rule, employee, employee_type):
            continue

        codes = _covered_codes(rule)
        applied = (sum(totals.values(), ZERO) if codes is None
                   else sum((v for k, v in totals.items() if k in codes), ZERO))
        applied = applied.quantize(CENT)

        ceilings = []
        unevaluated = None

        if rule.max_percent is not None:
            if rule.basis not in basis_cache:
                basis_cache[rule.basis] = basis_amount(
                    employee, employee_type, rule.basis, as_of_date)
            base, why = basis_cache[rule.basis]
            if base is None:
                unevaluated = why
            else:
                ceilings.append(((base * rule.max_percent / Decimal(100)).quantize(CENT),
                                 f'{rule.max_percent.normalize()}% of '
                                 f'{rule.get_basis_display().lower()} ({base})'))

        if rule.max_amount is not None:
            emp_currency = getattr(employee, 'currency', 'AED') or 'AED'
            if emp_currency != rule.amount_currency:
                # Converting at an assumed rate would make the ceiling a guess.
                if not ceilings:
                    unevaluated = (f'ceiling is in {rule.amount_currency} but this '
                                   f'employee is paid in {emp_currency}')
            else:
                ceilings.append((Decimal(rule.max_amount).quantize(CENT),
                                 f'{rule.amount_currency} {rule.max_amount}'))

        if not ceilings:
            results.append(RuleResult(rule, 'unevaluated',
                                      unevaluated or 'no ceiling could be resolved',
                                      None, None, applied, None))
            continue

        # Both ceilings can apply; the lower one binds.
        ceiling, label = min(ceilings, key=lambda c: c[0])
        base_val = basis_cache.get(rule.basis, (None, None))[0]
        if applied > ceiling:
            results.append(RuleResult(rule, 'breach',
                                      f'{applied} exceeds {label}',
                                      base_val, ceiling, applied,
                                      (applied - ceiling).quantize(CENT)))
        else:
            results.append(RuleResult(rule, 'pass', f'{applied} within {label}',
                                      base_val, ceiling, applied, ZERO))
    return results


def check_proposed(employee, employee_type, code, amount, year, month):
    """Would adding `amount` under `code` breach anything?

    Returns (blocking, warning, unevaluated) - three lists of RuleResult.
    `blocking` non-empty means the caller must refuse the entry.
    """
    from .models import DeductionRule

    results = evaluate(employee, employee_type, year, month,
                       extra={code: Decimal(str(amount))})
    breaches = [r for r in results if r.outcome == 'breach']
    return (
        [r for r in breaches if r.rule.enforcement == DeductionRule.ENFORCE_BLOCK],
        [r for r in breaches if r.rule.enforcement == DeductionRule.ENFORCE_WARN],
        [r for r in results if r.outcome == 'unevaluated'],
    )


def check_month(year, month, rules=None):
    """Evaluate every payroll employee for a month.

    Returns a list of per-employee dicts, breaches first. Used by the
    pre-payroll check screen.
    """
    from attendance.models import Employee, RemoteEmployee

    rules = active_rules(year, month) if rules is None else rules
    if not rules:
        return []

    inhouse = list(Employee.objects.filter(is_active=True,
                                           department__in=['Admin', 'Sales']).order_by('name'))
    tcr = {(e.tcr_id or '').strip() for e in inhouse if (e.tcr_id or '').strip()}
    remote_qs = RemoteEmployee.objects.filter(is_active=True)
    if tcr:
        remote_qs = remote_qs.exclude(tcr_id__in=tcr)

    out = []
    for emp, emp_type in ([(e, 'inhouse') for e in inhouse]
                          + [(e, 'remote') for e in remote_qs.order_by('name')]):
        results = evaluate(emp, emp_type, year, month, rules=rules)
        if not results:
            continue
        out.append({
            'employee': emp,
            'employee_type': emp_type,
            'results': results,
            'breaches': [r for r in results if r.outcome == 'breach'],
            'unevaluated': [r for r in results if r.outcome == 'unevaluated'],
        })
    out.sort(key=lambda r: (-len(r['breaches']), -len(r['unevaluated']),
                            r['employee'].name.lower()))
    return out
