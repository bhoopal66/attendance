"""
Profitability service — Phase 11.

Pure query/aggregation logic:

    Total Cost (AED)   = Salary(AED) + Employer Cost(AED) + Commission Paid(AED)
    Contribution (AED) = Derived Revenue − Total Cost
    ROI %              = Contribution ÷ Total Cost × 100
    Cost per Account   = Total Cost ÷ Achieved Accounts

FX convention (per ExchangeRate model): 1 AED = <rate> foreign units,
so foreign → AED is  amount / rate.

Agents whose currency has NO exchange rate for the month are flagged
(fx_missing=True) and excluded from AED totals — never silently zeroed.

Assumption (stated in Phase 11 plan): EmployerCostSetup amounts are AED.
Commission uses flat per-account rates via Bank.charge_for_currency();
tiered INR/NPR commission is intentionally not duplicated here (v1).

Deploy as: payroll/services_profitability.py
"""

import calendar
from datetime import date
from decimal import Decimal

ZERO = Decimal('0')
CENT = Decimal('0.01')


def _q2(value):
    """Quantise to 2dp for money display."""
    return (value or ZERO).quantize(CENT)


# ── FX ────────────────────────────────────────────────────────────────────────

def _fx_rates(year, month):
    """{currency: Decimal rate} for the month. 1 AED = rate foreign units."""
    from payroll.models import ExchangeRate
    return {
        r.currency: r.rate
        for r in ExchangeRate.objects.filter(year=year, month=month)
        if r.rate and r.rate > 0
    }


def to_aed(amount, currency, rates):
    """
    Convert amount in `currency` to AED using the month's rates.
    Returns (aed_amount, ok). AED passes through. Missing rate → (None, False).
    """
    amount = amount or ZERO
    if not currency or currency == 'AED':
        return amount, True
    rate = rates.get(currency)
    if not rate:
        return None, False
    return amount / rate, True


# ── Cost components ───────────────────────────────────────────────────────────

def _employer_cost_maps(year, month):
    """
    Latest EmployerCostSetup effective on or before the month end, per person.

    Returns ({employee_id: Decimal}, {remote_employee_id: Decimal}) in AED
    (assumption: cost setups are entered in AED).
    """
    from attendance.models import EmployerCostSetup

    month_end = date(year, month, calendar.monthrange(year, month)[1])

    inhouse, remote = {}, {}
    qs = (
        EmployerCostSetup.objects
        .filter(effective_from__lte=month_end)
        .order_by('effective_from', 'created_at')   # later rows overwrite earlier
    )
    for cs in qs:
        total = cs.total_monthly_cost or ZERO
        if cs.employee_id:
            inhouse[cs.employee_id] = total
        elif cs.remote_employee_id:
            remote[cs.remote_employee_id] = total
    return inhouse, remote


def _commission_maps(year, month):
    """
    Commission paid per person for the month, in the PERSON'S currency:
        Σ submission_count × bank.charge_for_currency(person.currency)

    Returns ({employee_id: Decimal}, {remote_employee_id: Decimal}).
    (Flat per-account rates; tiered commission not applied in v1.)
    """
    from payroll.models import BankSubmission

    inhouse, remote = {}, {}
    qs = (
        BankSubmission.objects
        .filter(year=year, month=month)
        .select_related('bank', 'employee', 'remote_employee')
    )
    for sub in qs:
        person = sub.employee or sub.remote_employee
        if person is None or not sub.submission_count:
            continue
        charge = sub.bank.charge_for_currency(person.currency or 'AED') or ZERO
        amount = sub.submission_count * charge
        if sub.employee_id:
            inhouse[sub.employee_id] = inhouse.get(sub.employee_id, ZERO) + amount
        else:
            remote[sub.remote_employee_id] = remote.get(sub.remote_employee_id, ZERO) + amount
    return inhouse, remote


def _revenue_maps(year, month):
    """
    Derived revenue (AED) per person:
        Σ submission_count × bank.revenue_per_account   (null rate → 0)

    Returns ({employee_id: {'revenue': Decimal, 'accounts': int}}, {remote_...}).
    """
    from payroll.services_performance import _submission_totals
    return _submission_totals(year, month)


# ── Row builder ───────────────────────────────────────────────────────────────

def _build_row(person, kind, rev, employer_cost, commission_native, rates):
    """
    Build one profitability row. Returns dict; fx_missing=True when the
    person's currency has no exchange rate this month (AED figures None).
    """
    currency = person.currency or 'AED'

    revenue = rev['revenue'] if rev else ZERO       # already AED
    accounts = rev['accounts'] if rev else 0

    salary_aed, ok_sal = to_aed(person.salary or ZERO, currency, rates)
    commission_aed, ok_com = to_aed(commission_native or ZERO, currency, rates)
    fx_missing = not (ok_sal and ok_com)

    row = {
        'kind':          kind,
        'person_id':     person.pk,
        'ref':           getattr(person, 'person_id', None) or getattr(person, 'extension_id', ''),
        'name':          person.name,
        'team':          person.team or '',
        'currency':      currency,
        'accounts':      accounts,
        'revenue':       _q2(revenue),
        'fx_missing':    fx_missing,
        'salary_aed':        None,
        'employer_cost_aed': _q2(employer_cost),
        'commission_aed':    None,
        'total_cost':        None,
        'contribution':      None,
        'roi_pct':           None,
        'cost_per_account':  None,
    }
    if fx_missing:
        return row

    total_cost = (salary_aed or ZERO) + (employer_cost or ZERO) + (commission_aed or ZERO)
    contribution = revenue - total_cost

    row['salary_aed'] = _q2(salary_aed)
    row['commission_aed'] = _q2(commission_aed)
    row['total_cost'] = _q2(total_cost)
    row['contribution'] = _q2(contribution)
    row['roi_pct'] = round(float(contribution * 100 / total_cost), 1) if total_cost > 0 else None
    row['cost_per_account'] = _q2(total_cost / accounts) if accounts else None
    return row


# ── Public API ────────────────────────────────────────────────────────────────

def month_profitability(year, month, include_inactive=False):
    """
    Full profitability dataset for a month.

    Returns dict:
        rows         — per-agent rows sorted by contribution desc
                       (fx_missing rows last)
        teams        — team rollups sorted by contribution desc
                       (AED-complete rows only)
        summary      — company totals (AED-complete rows only)
        fx_missing   — list of {name, ref, currency} needing an exchange rate
    """
    from attendance.models import Employee, RemoteEmployee

    rates = _fx_rates(year, month)
    rev_in, rev_rem = _revenue_maps(year, month)
    cost_in, cost_rem = _employer_cost_maps(year, month)
    com_in, com_rem = _commission_maps(year, month)

    emp_qs = Employee.objects.all() if include_inactive else Employee.objects.filter(is_active=True)
    remp_qs = RemoteEmployee.objects.all() if include_inactive else RemoteEmployee.objects.filter(is_active=True)

    rows = []
    for emp in emp_qs:
        rev = rev_in.get(emp.pk)
        # include anyone with activity or any cost signal
        if not rev and emp.pk not in cost_in and not (emp.salary and emp.salary > 0):
            continue
        rows.append(_build_row(
            emp, 'inhouse', rev,
            cost_in.get(emp.pk, ZERO), com_in.get(emp.pk, ZERO), rates,
        ))
    for remp in remp_qs:
        rev = rev_rem.get(remp.pk)
        if not rev and remp.pk not in cost_rem and not (remp.salary and remp.salary > 0):
            continue
        rows.append(_build_row(
            remp, 'remote', rev,
            cost_rem.get(remp.pk, ZERO), com_rem.get(remp.pk, ZERO), rates,
        ))

    rows.sort(key=lambda r: (r['fx_missing'], -(r['contribution'] if r['contribution'] is not None else ZERO)))

    fx_missing = [
        {'name': r['name'], 'ref': r['ref'], 'currency': r['currency']}
        for r in rows if r['fx_missing']
    ]
    complete = [r for r in rows if not r['fx_missing']]

    # ── team rollups (complete rows only) ─────────────────────────────────────
    teams = {}
    for r in complete:
        key = r['team'] or '— Unassigned —'
        agg = teams.setdefault(key, {
            'team': key, 'members': 0, 'accounts': 0,
            'revenue': ZERO, 'total_cost': ZERO, 'contribution': ZERO,
        })
        agg['members'] += 1
        agg['accounts'] += r['accounts']
        agg['revenue'] += r['revenue']
        agg['total_cost'] += r['total_cost']
        agg['contribution'] += r['contribution']

    team_rows = []
    for agg in teams.values():
        agg['revenue'] = _q2(agg['revenue'])
        agg['total_cost'] = _q2(agg['total_cost'])
        agg['contribution'] = _q2(agg['contribution'])
        agg['roi_pct'] = (
            round(float(agg['contribution'] * 100 / agg['total_cost']), 1)
            if agg['total_cost'] > 0 else None
        )
        team_rows.append(agg)
    team_rows.sort(key=lambda t: -t['contribution'])

    # ── company summary (complete rows only) ──────────────────────────────────
    total_revenue = _q2(sum((r['revenue'] for r in complete), ZERO))
    total_cost = _q2(sum((r['total_cost'] for r in complete), ZERO))
    total_contribution = _q2(total_revenue - total_cost)
    summary = {
        'people':             len(rows),
        'people_complete':    len(complete),
        'people_fx_missing':  len(fx_missing),
        'total_accounts':     sum(r['accounts'] for r in complete),
        'total_revenue':      total_revenue,
        'total_cost':         total_cost,
        'total_salary':       _q2(sum((r['salary_aed'] for r in complete), ZERO)),
        'total_employer_cost': _q2(sum((r['employer_cost_aed'] for r in complete), ZERO)),
        'total_commission':   _q2(sum((r['commission_aed'] for r in complete), ZERO)),
        'total_contribution': total_contribution,
        'overall_roi':        (
            round(float(total_contribution * 100 / total_cost), 1)
            if total_cost > 0 else None
        ),
        'profitable_count':   sum(1 for r in complete if r['contribution'] > 0),
        'lossmaking_count':   sum(1 for r in complete if r['contribution'] < 0),
    }

    return {
        'rows':       rows,
        'teams':      team_rows,
        'summary':    summary,
        'fx_missing': fx_missing,
    }
