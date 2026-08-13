"""
Performance service — Phase 10.

Pure query/aggregation logic for targets vs achievement reporting.
No view or template code here; unit-testable in isolation.

Deploy as: payroll/services_performance.py
"""

from decimal import Decimal

from django.db.models import Sum, F, Case, When, Value, DecimalField


# ── Status tiers ──────────────────────────────────────────────────────────────

def status_for_pct(pct):
    """
    Map an achievement percentage to a status tier.
        None      → 'no_target'
        ≥ 100     → 'achieved'
        70–99.9   → 'near'
        < 70      → 'below'
    """
    if pct is None:
        return 'no_target'
    if pct >= 100:
        return 'achieved'
    if pct >= 70:
        return 'near'
    return 'below'


STATUS_LABELS = {
    'achieved':  'Achieved',
    'near':      'Near Target',
    'below':     'Below Target',
    'no_target': 'No Target',
}


# ── Internal aggregation helpers ──────────────────────────────────────────────

_REVENUE_EXPR = Sum(
    Case(
        When(
            bank__revenue_per_account__isnull=False,
            then=F('submission_count') * F('bank__revenue_per_account'),
        ),
        default=Value(Decimal('0')),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
)


def _submission_totals(year, month):
    """
    Aggregate BankSubmission for the month in TWO queries (one per FK side).

    Returns:
        inhouse: {employee_id: {'accounts': int, 'revenue': Decimal}}
        remote:  {remote_employee_id: {...}}
    """
    from payroll.models import BankSubmission

    inhouse, remote = {}, {}

    rows = (
        BankSubmission.objects
        .filter(year=year, month=month, employee__isnull=False)
        .values('employee_id')
        .annotate(accounts=Sum('submission_count'), revenue=_REVENUE_EXPR)
    )
    for r in rows:
        inhouse[r['employee_id']] = {
            'accounts': r['accounts'] or 0,
            'revenue':  r['revenue'] or Decimal('0'),
        }

    rows = (
        BankSubmission.objects
        .filter(year=year, month=month, remote_employee__isnull=False)
        .values('remote_employee_id')
        .annotate(accounts=Sum('submission_count'), revenue=_REVENUE_EXPR)
    )
    for r in rows:
        remote[r['remote_employee_id']] = {
            'accounts': r['accounts'] or 0,
            'revenue':  r['revenue'] or Decimal('0'),
        }

    return inhouse, remote


def _target_maps(year, month):
    """Returns ({employee_id: EmployeeTarget}, {remote_employee_id: EmployeeTarget})."""
    from payroll.models import EmployeeTarget

    inhouse, remote = {}, {}
    for t in EmployeeTarget.objects.filter(year=year, month=month):
        if t.employee_id:
            inhouse[t.employee_id] = t
        elif t.remote_employee_id:
            remote[t.remote_employee_id] = t
    return inhouse, remote


def _make_row(person, kind, target_obj, sub):
    """Build one performance row dict for a person."""
    target = target_obj.target_accounts if target_obj else 0
    achieved = sub['accounts'] if sub else 0
    revenue = sub['revenue'] if sub else Decimal('0')

    pct = round(achieved * 100 / target, 1) if target else None
    status = status_for_pct(pct)

    return {
        'kind':         kind,                      # 'inhouse' | 'remote'
        'person_id':    person.pk,
        'ref':          getattr(person, 'person_id', None) or getattr(person, 'extension_id', ''),
        'name':         person.name,
        'team':         person.team or '',
        'target':       target,
        'target_id':    target_obj.pk if target_obj else None,
        'achieved':     achieved,
        'pct':          pct,
        'revenue':      revenue,
        'status':       status,
        'status_label': STATUS_LABELS[status],
    }


# ── Public API ────────────────────────────────────────────────────────────────

def month_performance(year, month, include_inactive=False):
    """
    Full performance dataset for a month.

    Returns dict:
        rows        — list of per-person row dicts (in-house first, then remote),
                      sorted by achievement pct desc (no-target rows last)
        teams       — list of team rollup dicts sorted by pct desc
        summary     — totals: target, achieved, pct, revenue,
                      counts per status tier
    """
    from attendance.models import Employee, RemoteEmployee

    sub_in, sub_rem = _submission_totals(year, month)
    tgt_in, tgt_rem = _target_maps(year, month)

    emp_qs = Employee.objects.all() if include_inactive else Employee.objects.filter(is_active=True)
    remp_qs = RemoteEmployee.objects.all() if include_inactive else RemoteEmployee.objects.filter(is_active=True)

    rows = []
    for emp in emp_qs:
        row = _make_row(emp, 'inhouse', tgt_in.get(emp.pk), sub_in.get(emp.pk))
        # skip people with no target AND no submissions — not sales agents
        if row['target'] or row['achieved']:
            rows.append(row)
    for remp in remp_qs:
        row = _make_row(remp, 'remote', tgt_rem.get(remp.pk), sub_rem.get(remp.pk))
        if row['target'] or row['achieved']:
            rows.append(row)

    # sort: highest pct first, no-target rows at the bottom (by achieved desc)
    rows.sort(key=lambda r: (r['pct'] is None, -(r['pct'] or 0), -r['achieved']))

    # ── team rollups ──────────────────────────────────────────────────────────
    teams = {}
    for r in rows:
        key = r['team'] or '— Unassigned —'
        agg = teams.setdefault(key, {
            'team': key, 'members': 0,
            'target': 0, 'achieved': 0, 'revenue': Decimal('0'),
        })
        agg['members'] += 1
        agg['target'] += r['target']
        agg['achieved'] += r['achieved']
        agg['revenue'] += r['revenue']

    team_rows = []
    for agg in teams.values():
        pct = round(agg['achieved'] * 100 / agg['target'], 1) if agg['target'] else None
        agg['pct'] = pct
        agg['status'] = status_for_pct(pct)
        agg['status_label'] = STATUS_LABELS[agg['status']]
        team_rows.append(agg)
    team_rows.sort(key=lambda t: (t['pct'] is None, -(t['pct'] or 0)))

    # ── summary ───────────────────────────────────────────────────────────────
    total_target = sum(r['target'] for r in rows)
    total_achieved = sum(r['achieved'] for r in rows)
    total_revenue = sum((r['revenue'] for r in rows), Decimal('0'))
    overall_pct = round(total_achieved * 100 / total_target, 1) if total_target else None

    status_counts = {'achieved': 0, 'near': 0, 'below': 0, 'no_target': 0}
    for r in rows:
        status_counts[r['status']] += 1

    return {
        'rows':    rows,
        'teams':   team_rows,
        'summary': {
            'people':         len(rows),
            'total_target':   total_target,
            'total_achieved': total_achieved,
            'overall_pct':    overall_pct,
            'total_revenue':  total_revenue,
            'status_counts':  status_counts,
        },
    }


def person_trend(person, kind, end_year, end_month, months=12):
    """
    Trend series for one person, ending at (end_year, end_month) inclusive.

    kind: 'inhouse' | 'remote'

    Returns list of dicts oldest→newest:
        { 'year', 'month', 'target', 'achieved', 'pct', 'revenue', 'status' }
    """
    from payroll.models import BankSubmission, EmployeeTarget

    # month window
    end_idx = end_year * 12 + (end_month - 1)
    idxs = list(range(end_idx - months + 1, end_idx + 1))
    window = [(i // 12, i % 12 + 1) for i in idxs]
    years = {y for y, _ in window}

    fk = 'employee' if kind == 'inhouse' else 'remote_employee'
    person_filter = {f'{fk}_id': person.pk}

    # targets in window
    tgt_map = {
        (t.year, t.month): t.target_accounts
        for t in EmployeeTarget.objects.filter(year__in=years, **person_filter)
    }

    # submissions in window
    sub_map = {}
    rows = (
        BankSubmission.objects
        .filter(year__in=years, **person_filter)
        .values('year', 'month')
        .annotate(accounts=Sum('submission_count'), revenue=_REVENUE_EXPR)
    )
    for r in rows:
        sub_map[(r['year'], r['month'])] = {
            'accounts': r['accounts'] or 0,
            'revenue':  r['revenue'] or Decimal('0'),
        }

    series = []
    for (y, m) in window:
        target = tgt_map.get((y, m), 0)
        sub = sub_map.get((y, m))
        achieved = sub['accounts'] if sub else 0
        revenue = sub['revenue'] if sub else Decimal('0')
        pct = round(achieved * 100 / target, 1) if target else None
        series.append({
            'year': y, 'month': m,
            'target': target, 'achieved': achieved,
            'pct': pct, 'revenue': revenue,
            'status': status_for_pct(pct),
        })
    return series
