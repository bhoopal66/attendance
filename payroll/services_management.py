"""
Management Dashboard service — Phase 12.

Aggregates the existing Phase 9–11 services into one executive dataset.
No business math is duplicated here — KPIs come from services_performance
and services_profitability; alerts reuse the Phase 9 exception centre.

Deploy as: payroll/services_management.py
"""

from datetime import date
from decimal import Decimal

ZERO = Decimal('0')

EXPIRY_WINDOW_DAYS = 60      # documents expiring within this window are alerted
LOW_COMPLETENESS_PCT = 60    # profiles below this are listed in data health
TOP_N = 5


def management_snapshot(year, month):
    """
    Full dashboard dataset for a month.

    Returns dict with keys:
        kpis          — headline numbers
        run           — PayrollRun for the month (stage, labels)
        top_by_pct    — top N agents by achievement % (targets set)
        bottom_by_pct — bottom N agents by achievement %
        top_by_contribution / bottom_by_contribution — from profitability
        alerts        — list of {severity, title, detail, count, link_key}
        data_health   — completeness + setup-gap metrics
    """
    from attendance.models import Employee, RemoteEmployee, EmployeeDocument, Recoverable
    from payroll.models import PayrollRun, Bank
    from payroll.services_performance import month_performance
    from payroll.services_profitability import month_profitability
    from payroll.views_payroll_run import _build_exception_report

    perf = month_performance(year, month)
    prof = month_profitability(year, month)
    exceptions = _build_exception_report(year, month)
    run = PayrollRun.get_or_create_for_month(year, month)

    # ── KPIs ──────────────────────────────────────────────────────────────────
    inhouse_count = Employee.objects.filter(is_active=True).count()
    remote_count = RemoteEmployee.objects.filter(is_active=True).count()

    kpis = {
        'headcount_inhouse':  inhouse_count,
        'headcount_remote':   remote_count,
        'headcount_total':    inhouse_count + remote_count,
        'agents_tracked':     perf['summary']['people'],
        'total_target':       perf['summary']['total_target'],
        'total_achieved':     perf['summary']['total_achieved'],
        'achievement_pct':    perf['summary']['overall_pct'],
        'total_revenue':      prof['summary']['total_revenue'],
        'total_cost':         prof['summary']['total_cost'],
        'total_contribution': prof['summary']['total_contribution'],
        'overall_roi':        prof['summary']['overall_roi'],
        'profitable_count':   prof['summary']['profitable_count'],
        'lossmaking_count':   prof['summary']['lossmaking_count'],
    }

    # ── Performers ────────────────────────────────────────────────────────────
    with_target = [r for r in perf['rows'] if r['pct'] is not None]
    top_by_pct = with_target[:TOP_N]                      # perf rows already sorted pct desc
    bottom_by_pct = list(reversed(with_target[-TOP_N:])) if with_target else []
    # avoid overlap when fewer than 2×N agents
    if len(with_target) <= TOP_N:
        bottom_by_pct = []

    prof_complete = [r for r in prof['rows'] if not r['fx_missing']]
    top_by_contribution = prof_complete[:TOP_N]           # prof rows sorted contribution desc
    bottom_by_contribution = (
        list(reversed(prof_complete[-TOP_N:])) if len(prof_complete) > TOP_N else []
    )

    # ── Alerts ────────────────────────────────────────────────────────────────
    alerts = []

    for blk in exceptions['blockers']:
        alerts.append({
            'severity': 'blocker',
            'title':    blk['check'],
            'detail':   blk.get('detail', ''),
            'items':    blk['items'][:8],
            'count':    len(blk['items']),
            'link_key': 'run',
        })
    for wrn in exceptions['warnings']:
        alerts.append({
            'severity': 'warning',
            'title':    wrn['check'],
            'detail':   wrn.get('detail', ''),
            'items':    wrn['items'][:8],
            'count':    len(wrn['items']),
            'link_key': 'run',
        })

    # documents expired / expiring soon
    today = date.today()
    doc_alerts_expired, doc_alerts_expiring = [], []
    docs = EmployeeDocument.objects.filter(expiry_date__isnull=False).select_related(
        'employee', 'remote_employee')
    for doc in docs:
        person = doc.employee or doc.remote_employee
        if person is None or not getattr(person, 'is_active', True):
            continue
        d = doc.days_to_expiry
        if d is None:
            continue
        label = f'{person.name} — {doc.get_document_type_display()} ({doc.expiry_date})'
        if d < 0:
            doc_alerts_expired.append(label)
        elif d <= EXPIRY_WINDOW_DAYS:
            doc_alerts_expiring.append(label)
    if doc_alerts_expired:
        alerts.append({
            'severity': 'blocker',
            'title':    'Documents expired',
            'detail':   'Active staff with expired documents.',
            'items':    doc_alerts_expired[:8],
            'count':    len(doc_alerts_expired),
            'link_key': 'documents',
        })
    if doc_alerts_expiring:
        alerts.append({
            'severity': 'warning',
            'title':    f'Documents expiring within {EXPIRY_WINDOW_DAYS} days',
            'detail':   'Renewals to schedule.',
            'items':    doc_alerts_expiring[:8],
            'count':    len(doc_alerts_expiring),
            'link_key': 'documents',
        })

    # FX rates missing (from profitability)
    if prof['fx_missing']:
        alerts.append({
            'severity': 'warning',
            'title':    'Exchange rates missing',
            'detail':   f'Agents excluded from AED profitability totals for {month}/{year}.',
            'items':    [f"{f['name']} ({f['currency']})" for f in prof['fx_missing']][:8],
            'count':    len(prof['fx_missing']),
            'link_key': 'exchange',
        })

    # banks without revenue rate
    banks_no_rev = list(
        Bank.objects.filter(is_active=True, revenue_per_account__isnull=True)
        .values_list('name', flat=True)
    )
    if banks_no_rev:
        alerts.append({
            'severity': 'warning',
            'title':    'Banks without revenue rate',
            'detail':   'Submissions to these banks earn zero derived revenue until a rate is set.',
            'items':    banks_no_rev[:8],
            'count':    len(banks_no_rev),
            'link_key': 'banks',
        })

    severity_order = {'blocker': 0, 'warning': 1}
    alerts.sort(key=lambda a: (severity_order.get(a['severity'], 2), -a['count']))

    # ── Data health ───────────────────────────────────────────────────────────
    active_emps = list(Employee.objects.filter(is_active=True))

    completeness_vals = []
    low_completeness = []
    for emp in active_emps:
        try:
            pct = emp.profile_completeness
        except Exception:
            continue
        if pct is None:
            continue
        completeness_vals.append(pct)
        if pct < LOW_COMPLETENESS_PCT:
            low_completeness.append({'name': emp.name, 'ref': emp.person_id, 'pct': pct})
    low_completeness.sort(key=lambda x: x['pct'])

    missing_bank = sum(
        1 for e in active_emps
        if (e.currency or 'AED') == 'AED' and (not e.bank_name or not e.bank_account_number)
    )
    no_cost_setup = sum(1 for e in active_emps if not e.cost_setups.exists())

    open_recs = Recoverable.objects.filter(status='active')
    rec_outstanding = sum((r.outstanding_balance for r in open_recs), ZERO)

    data_health = {
        'avg_completeness':  round(sum(completeness_vals) / len(completeness_vals), 1)
                             if completeness_vals else None,
        'low_completeness':  low_completeness[:TOP_N],
        'low_completeness_count': len(low_completeness),
        'missing_bank':      missing_bank,
        'no_cost_setup':     no_cost_setup,
        'open_recoverables': open_recs.count(),
        'rec_outstanding':   rec_outstanding,
    }

    return {
        'kpis':                   kpis,
        'run':                    run,
        'top_by_pct':             top_by_pct,
        'bottom_by_pct':          bottom_by_pct,
        'top_by_contribution':    top_by_contribution,
        'bottom_by_contribution': bottom_by_contribution,
        'alerts':                 alerts,
        'blocker_count':          sum(1 for a in alerts if a['severity'] == 'blocker'),
        'warning_count':          sum(1 for a in alerts if a['severity'] == 'warning'),
        'data_health':            data_health,
    }
