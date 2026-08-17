"""
The profile header — status, alerts and key facts.

WHY THE HEADER IS A SERVICE
---------------------------
The old page opened with a "Profile Complete 96%" bar. Nobody has ever acted
on that number. Meanwhile an expired labour card sat invisible inside the
eighth card down. This module computes what actually needs attention so the
top of the page can carry it.

An alert here is a thing somebody has to DO. If nothing needs doing, this
returns an empty list and the template renders nothing — a permanently present
"all good" box is a box people stop reading, and then it is no longer able to
warn them.
"""

import datetime
import logging

logger = logging.getLogger('attendance')


def _fmt_money(value, currency='AED'):
    if value is None:
        return None
    return f'{currency} {float(value):,.0f}'


def header_alerts(employee, today=None):
    """Things that need doing, worst first. Empty when nothing does."""
    from .services_compliance import DOCUMENT_ROWS, expiry_tier, _latest_documents

    today = today or datetime.date.today()
    out = []

    latest = _latest_documents(employee)
    for doc_type, label, _has_number in DOCUMENT_ROWS:
        doc = latest.get(doc_type)
        expiry = doc.expiry_date if doc else None
        tier = expiry_tier(expiry, today)
        if tier == 'expired':
            out.append({
                'level': 'bad',
                'title': f'{label} expired {(today - expiry).days} days ago',
                'detail': f'{expiry:%d %b %Y}. Renewal not recorded.',
                'action': 'Documents',
            })
        elif tier == 'tier30':
            out.append({
                'level': 'warn',
                'title': f'{label} expires in {(expiry - today).days} days',
                'detail': f'{expiry:%d %b %Y}.',
                'action': 'Documents',
            })

    state = employee.compliance_review_state(today)
    if state == 'never':
        out.append({'level': 'warn',
                    'title': 'Compliance record has never been reviewed',
                    'detail': 'No one has confirmed these details are correct.',
                    'action': 'Compliance'})
    elif state == 'due':
        since = (today - employee.compliance_reviewed_at.date()).days
        out.append({'level': 'warn',
                    'title': f'Compliance record last reviewed {since} days ago',
                    'detail': 'Review is due every 90 days.',
                    'action': 'Compliance'})

    if employee.probation_state(today) == 'review_due':
        due = employee.probation_review_due
        out.append({'level': 'warn',
                    'title': 'Probation review is due',
                    'detail': f'Day 75 was {due:%d %b %Y}'
                              + (' (inferred from the joining date).'
                                 if employee.probation_is_inferred else '.'),
                    'action': 'Employment'})

    # Worst first: an expired document outranks a paperwork reminder.
    order = {'bad': 0, 'warn': 1}
    out.sort(key=lambda a: order.get(a['level'], 2))
    return out


def header_facts(employee, today=None):
    """The six things people open this page to find out.

    Every one of them can be None. A fact with no value is rendered as
    "not recorded" rather than dropped, because a missing salary and a
    salary of zero are different problems and only one of them is an error.
    """
    from .services_compliance import DOCUMENT_ROWS, expiry_tier, _latest_documents

    today = today or datetime.date.today()
    currency = getattr(employee, 'currency', 'AED') or 'AED'
    facts = []

    # 1. Salary, with the basic share — the number that drives encashment.
    full = basic = None
    try:
        from payroll.services_leave_earnings import wage_components
        full, basic, _src = wage_components(employee, today)
    except Exception:
        logger.debug('wage components unavailable for %s', employee.pk)
    share = (f'Basic {float(basic):,.0f} · {round(float(basic) / float(full) * 100)}%'
             if basic and full else 'Basic not recorded')
    facts.append({'key': 'Gross salary', 'value': _fmt_money(full, currency),
                  'sub': share})

    # 2 & 3. Leave balance and what it would cost to encash.
    try:
        from payroll.services_leave_earnings import leave_summary
        ls = leave_summary(employee, today)
        facts.append({
            'key': 'Leave balance',
            'value': (None if ls['balance_days'] is None
                      else f'{ls["balance_days"]:g} days'),
            'sub': (None if ls['accrued_days'] is None else
                    f'{ls["accrued_days"]:g} accrued − {ls["taken_days"]:g} taken'),
            'negative': ls['balance_days'] is not None and ls['balance_days'] < 0,
        })
        # The label has to name the BASE, not just the amount. "At basic rate"
        # was accurate when encashment was basic-salary-based; it is now 50% of
        # gross, and a stale label on a settlement figure is worse than no label.
        _sub = f'at {ls["policy_pct"]:g}% of gross'
        if ls.get('encashment_shortfall'):
            _sub += f' — {_fmt_money(ls["encashment_shortfall"], currency)} below basic-at-100%'
        facts.append({'key': 'Encashment exposure',
                      'value': _fmt_money(ls['encashment_value'], currency),
                      'sub': _sub if ls['encashment_value'] is not None
                             else 'no joining date on record'})
    except Exception:
        logger.debug('leave summary unavailable for %s', employee.pk)
        facts.append({'key': 'Leave balance', 'value': None, 'sub': None})
        facts.append({'key': 'Encashment exposure', 'value': None, 'sub': None})

    # 4. Probation, in words rather than a raw state name.
    state = employee.probation_state(today)
    end = employee.probation_end
    labels = {'none': 'Not applicable', 'in_probation': 'In probation',
              'review_due': 'Review due', 'passed': 'Passed'}
    facts.append({
        'key': 'Probation',
        'value': labels.get(state, state),
        'sub': (None if end is None else
                ('ends ' if state != 'passed' else 'ended ') + f'{end:%d %b %Y}'
                + (' · inferred' if employee.probation_is_inferred else '')),
        'negative': state == 'review_due',
    })

    # 5. Open recoverables.
    try:
        from .models import Recoverable
        field = ('employee' if employee.__class__.__name__ == 'Employee'
                 else 'remote_employee')
        open_rows = list(Recoverable.objects.filter(**{field: employee})
                         .exclude(status='settled'))
        total = sum(float(getattr(r, 'outstanding_amount', 0) or 0) for r in open_rows)
        facts.append({'key': 'Recoverables',
                      'value': _fmt_money(total, currency) if open_rows else _fmt_money(0, currency),
                      'sub': f'{len(open_rows)} open' if open_rows else 'none outstanding'})
    except Exception:
        logger.debug('recoverables unavailable for %s', employee.pk)
        facts.append({'key': 'Recoverables', 'value': None, 'sub': None})

    # 6. Documents — recorded vs the five that are tracked.
    latest = _latest_documents(employee)
    recorded = sum(1 for t, _l, _n in DOCUMENT_ROWS if t in latest)
    expired = sum(1 for t, _l, _n in DOCUMENT_ROWS
                  if expiry_tier(latest[t].expiry_date if t in latest else None,
                                 today) == 'expired')
    missing = len(DOCUMENT_ROWS) - recorded
    bits = []
    if expired:
        bits.append(f'{expired} expired')
    if missing:
        bits.append(f'{missing} not recorded')
    facts.append({'key': 'Documents', 'value': f'{recorded} of {len(DOCUMENT_ROWS)}',
                  'sub': ' · '.join(bits) or 'all current',
                  'negative': bool(expired)})
    return facts


def status_pills(employee, today=None):
    """Short badges for the identity bar — state, not detail."""
    from .services_compliance import DOCUMENT_ROWS, expiry_tier, _latest_documents

    today = today or datetime.date.today()
    pills = []

    active = getattr(employee, 'is_active', True)
    status = getattr(employee, 'employment_status', 'active') or 'active'
    if not active:
        pills.append({'label': 'Inactive', 'tone': 'bad'})
    elif status != 'active':
        pills.append({'label': status.replace('_', ' ').title(), 'tone': 'warn'})
    else:
        pills.append({'label': 'Active', 'tone': 'ok'})

    if getattr(employee, 'visa_type', ''):
        pills.append({'label': employee.get_visa_type_display(), 'tone': 'neutral'})
    if getattr(employee, 'contract_type', ''):
        pills.append({'label': employee.get_contract_type_display() + ' contract',
                      'tone': 'neutral'})

    latest = _latest_documents(employee)
    for doc_type, label, _n in DOCUMENT_ROWS:
        doc = latest.get(doc_type)
        if expiry_tier(doc.expiry_date if doc else None, today) == 'expired':
            pills.append({'label': f'{label} expired', 'tone': 'bad'})

    if employee.compliance_review_state(today) in ('never', 'due'):
        pills.append({'label': 'Compliance review overdue', 'tone': 'warn'})
    if employee.probation_state(today) == 'in_probation':
        pills.append({'label': 'In probation', 'tone': 'warn'})
    return pills


def build(employee, today=None):
    today = today or datetime.date.today()
    return {
        'profile_alerts': header_alerts(employee, today),
        'profile_facts': header_facts(employee, today),
        'profile_pills': status_pills(employee, today),
    }
