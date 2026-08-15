"""
Sunday Entitlement Engine — the central weekly-off rule.

WHY THIS MODULE IMPORTS NOTHING FROM DJANGO
-------------------------------------------
It takes dates and returns a verdict. No models, no queries, no settings.
That is deliberate:

  * it can be exercised exhaustively in milliseconds, which is how the
    specified scenarios plus tens of thousands of randomised periods actually
    get covered;
  * payroll, attendance, salary proration, leave, settlement, payslips and
    reports can all call the same function and cannot drift into private
    variants of "how many Sundays does this person get";
  * the day the weekly-off moves from Sunday to Friday, one policy value
    changes and no calculation code does.

`services_sunday_entitlement_db.py` holds the thin Django layer that reads an
employee's records and calls in here. Keep the split.

THE RULE, IN ONE SENTENCE
-------------------------
Total Sundays in the period, minus Sundays before the employee became
eligible, minus Sundays after they ceased to be eligible, minus Sundays inside
a period where an HR rule makes them unavailable.

STRICTLY AFTER, NEVER ON
------------------------
A Sunday falling ON the joining date or ON the rejoining date is NOT counted.
The comparison is `sunday > effective_date`, never `>=`. This is the single
detail most likely to be got wrong, and it is asserted directly in the tests.

TIMELINE, NOT ONE CUT-OFF DATE
------------------------------
Each Sunday is judged against the employee's actual state on that date, rather
than against a single "eligibility start". An employee who joins on the 5th,
takes leave from the 10th to the 23rd and returns on the 24th genuinely worked
the Sunday on the 9th; collapsing everything to one latest-date cut-off would
silently strip it. Adding unpaid leave, suspension or maternity later means
adding one more non-eligible span, not rewriting the rule.

NEVER days / 7
--------------
The count comes from walking the calendar. A period holds four or five Sundays
depending on where it starts, and dividing by seven gets that wrong about half
the time.
"""

import calendar
import datetime
from collections import namedtuple

SUNDAY = 6  # datetime.date.weekday(): Monday=0 ... Sunday=6


class SundayPolicy:
    """Configurable weekly-off policy.

    Defaults reproduce the UAE Sunday rule. Everything a future policy might
    need to change lives here, so the engine itself does not have to.
    """

    def __init__(self, weekday=SUNDAY, count_on_effective_date=False,
                 exclude_during_annual_leave=True,
                 exclude_after_last_working_date=True,
                 infer_rejoining_from_leave_end=True):
        self.weekday = weekday
        # False => "strictly after". Set True only if policy ever decides a
        # weekly-off falling ON the joining date should be paid.
        self.count_on_effective_date = count_on_effective_date
        self.exclude_during_annual_leave = exclude_during_annual_leave
        self.exclude_after_last_working_date = exclude_after_last_working_date
        # When a leave record carries no actual rejoining date, assume the
        # employee returned the day after the leave ended. Flagged in the
        # output as inferred so nobody mistakes it for a recorded fact.
        self.infer_rejoining_from_leave_end = infer_rejoining_from_leave_end

    @property
    def weekday_name(self):
        return calendar.day_name[self.weekday]


DEFAULT_POLICY = SundayPolicy()

#: One weekly-off date and why it counted or did not.
SundayVerdict = namedtuple('SundayVerdict', ['date', 'eligible', 'reason', 'rule'])

# Stable rule identifiers - safe to store and to filter reports on.
RULE_ELIGIBLE = 'eligible'
RULE_BEFORE_JOINING = 'before_joining'
RULE_ON_JOINING = 'on_joining_date'
RULE_ANNUAL_LEAVE = 'annual_leave'
RULE_ON_REJOINING = 'on_rejoining_date'
RULE_AFTER_LAST_DAY = 'after_last_working_date'

BASIS_EXISTING = 'Existing Employee'
BASIS_NEW_JOINER = 'New Joiner'
BASIS_REJOINED = 'Returned from Annual Leave'
BASIS_TERMINATED = 'Terminated Employee'
BASIS_JOINER_LEAVE = 'New Joiner + Annual Leave'


def _as_date(value):
    """Accept a date, datetime or ISO string. None for empty values."""
    if value is None or value == '':
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str):
        return datetime.date.fromisoformat(value.strip())
    raise TypeError('Cannot interpret %r as a date' % (value,))


def weekly_off_dates(period_start, period_end, policy=DEFAULT_POLICY):
    """Every weekly-off date in the period, both endpoints inclusive."""
    start, end = _as_date(period_start), _as_date(period_end)
    if start is None or end is None:
        raise ValueError('Both period start and period end are required')
    if end < start:
        raise ValueError('Payroll period ends (%s) before it starts (%s)' % (end, start))

    # Jump to the first matching weekday rather than testing every day.
    first = start + datetime.timedelta(days=(policy.weekday - start.weekday()) % 7)
    out, cur = [], first
    while cur <= end:
        out.append(cur)
        cur += datetime.timedelta(days=7)
    return out


def _leave_spans(annual_leave_records, actual_rejoining_date, policy):
    """[(from, through, rejoin_was_recorded)] - dates the employee is NOT
    eligible because of annual leave.

    The span runs from the leave start to the rejoining date INCLUSIVE: a
    weekly-off falling on the day they came back is not counted either.
    """
    spans = []
    for rec in (annual_leave_records or []):
        if isinstance(rec, dict):
            start = _as_date(rec.get('start_date'))
            end = _as_date(rec.get('end_date'))
            rejoin = _as_date(rec.get('actual_rejoining_date'))
        else:
            start = _as_date(getattr(rec, 'start_date', None))
            end = _as_date(getattr(rec, 'end_date', None))
            rejoin = _as_date(getattr(rec, 'actual_rejoining_date', None))
        if start is None or end is None:
            continue
        if end < start:
            raise ValueError('Annual leave ends (%s) before it starts (%s)' % (end, start))

        recorded = rejoin is not None
        if not recorded:
            # A single loose rejoining date applies to the leave it plausibly
            # follows, not to every leave record on file.
            loose = _as_date(actual_rejoining_date)
            if loose is not None and loose > end:
                rejoin, recorded = loose, True
            elif policy.infer_rejoining_from_leave_end:
                rejoin = end + datetime.timedelta(days=1)
            else:
                rejoin = end
        if rejoin < start:
            raise ValueError('Rejoining date (%s) precedes the leave start (%s)' % (rejoin, start))
        spans.append((start, rejoin, recorded))
    return spans


def calculate_sunday_entitlement(payroll_period_start, payroll_period_end,
                                 date_of_joining=None, annual_leave_records=None,
                                 actual_rejoining_date=None, last_working_date=None,
                                 employee_status=None, policy=DEFAULT_POLICY,
                                 employee_id=None, employee_name=None):
    """Weekly-off entitlement for one employee over one payroll period.

    Every Sunday comes back with the reason it counted or did not, so the
    figure can always be explained rather than merely trusted.
    """
    start = _as_date(payroll_period_start)
    end = _as_date(payroll_period_end)
    joining = _as_date(date_of_joining)
    last_day = _as_date(last_working_date)

    if joining and last_day and last_day < joining:
        raise ValueError('Last working date (%s) precedes the joining date (%s)'
                         % (last_day, joining))

    all_sundays = weekly_off_dates(start, end, policy)
    spans = _leave_spans(annual_leave_records, actual_rejoining_date, policy)

    joined_in_period = bool(joining and start <= joining <= end)
    rejoins_in_period = [s for s in spans if start <= s[1] <= end]
    terminated_in_period = bool(last_day and start <= last_day <= end)

    # The headline "eligibility start" the payroll screen shows. The per-Sunday
    # verdicts below do the real work; this is the summary of them.
    if joined_in_period:
        eligibility_start = joining
        basis = BASIS_JOINER_LEAVE if rejoins_in_period else BASIS_NEW_JOINER
    elif rejoins_in_period:
        eligibility_start = max(s[1] for s in rejoins_in_period)
        basis = BASIS_REJOINED
    else:
        eligibility_start = start - datetime.timedelta(days=1)
        basis = BASIS_EXISTING
    if terminated_in_period:
        basis = BASIS_TERMINATED if basis == BASIS_EXISTING else basis + ' + Terminated'

    verdicts = []
    for sunday in all_sundays:
        rule = RULE_ELIGIBLE
        reason = 'Within %s entitlement' % policy.weekday_name

        if joining and sunday < joining:
            rule, reason = RULE_BEFORE_JOINING, 'Before date of joining'
        elif joining and sunday == joining and not policy.count_on_effective_date:
            rule, reason = RULE_ON_JOINING, 'Falls on the date of joining'
        elif policy.exclude_during_annual_leave:
            for span_start, rejoin, recorded in spans:
                if span_start <= sunday <= rejoin:
                    suffix = '' if recorded else ' (rejoining date inferred from leave end)'
                    if sunday == rejoin and not policy.count_on_effective_date:
                        rule = RULE_ON_REJOINING
                        reason = 'Falls on the rejoining date' + suffix
                    elif sunday < rejoin:
                        rule = RULE_ANNUAL_LEAVE
                        reason = 'On annual leave / before rejoining' + suffix
                    break

        if (rule == RULE_ELIGIBLE and last_day
                and policy.exclude_after_last_working_date and sunday > last_day):
            rule, reason = RULE_AFTER_LAST_DAY, 'After last working date'

        verdicts.append(SundayVerdict(sunday, rule == RULE_ELIGIBLE, reason, rule))

    eligible = [v.date for v in verdicts if v.eligible]
    excluded = [v.date for v in verdicts if not v.eligible]

    return {
        'employee_id': employee_id,
        'employee_name': employee_name,
        'payroll_period_start': start,
        'payroll_period_end': end,
        'weekday_name': policy.weekday_name,
        'total_sundays_in_period': len(all_sundays),
        'all_sunday_dates': all_sundays,
        'sunday_eligibility_start_date': eligibility_start,
        'basis': basis,
        'employee_status': employee_status or basis,
        'eligible_sunday_dates': eligible,
        'excluded_sunday_dates': excluded,
        'eligible_sunday_count': len(eligible),
        'excluded_sunday_count': len(excluded),
        'exclusion_reasons': dict((v.date.isoformat(), v.reason)
                                  for v in verdicts if not v.eligible),
        'verdicts': verdicts,
        'audit_rows': [{'sunday': v.date.isoformat(),
                        'status': 'Eligible' if v.eligible else 'Excluded',
                        'reason': v.reason, 'rule': v.rule} for v in verdicts],
    }
