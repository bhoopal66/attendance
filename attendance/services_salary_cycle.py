"""Resolving and changing an employee's pay cycle over time.

WHY THIS EXISTS
----------------
`Employee.salary_cycle_start_day` / `RemoteEmployee.salary_cycle_start_day`
used to be a single live field: changing it changed the pay period for
every month, past and future, because payroll always read today's value.
Recalculating an old month after a cycle change therefore silently used the
*current* cycle instead of whatever was actually in force back then.

This module adds a resolver that answers "what pay period applies to this
employee for calendar month X" using two effective-dated tables —
`SalaryCycleDefault` (company-wide, "for everyone") and `SalaryCycleHistory`
(a specific employee's override) — while leaving the legacy field in place
as the pre-history fallback. With no rows in either table, resolution is
byte-for-byte identical to reading the field directly, so introducing this
module changes no payroll figure until someone actually records a change.

WHY EFFECTIVE_DATE, NOT EFFECTIVE MONTH
-----------------------------------------
A cycle change is dated to an exact day, not just a month. This matters:
switching from a full-month cycle to a 21st-to-20th cycle with only
month-level precision would make the transition month's *natural* period
overlap the previous one by up to ~10 days (the same days get paid twice)
or leave a gap (some days never get paid), depending on direction. Storing
an exact `effective_date` and CLIPPING the natural period on either side of
it — see `get_employee_pay_period()` below — guarantees every calendar day
belongs to exactly one pay period, never two, never zero, regardless of
which day of the month a change happens to land on.

THE ONE SUPPORTED WAY TO CHANGE A CYCLE
----------------------------------------
`set_employee_cycle_override()` / `set_default_cycle()`. Both create (or
correct, via upsert) one dated row and keep the legacy field in sync with
whatever is now the most-recent entry, so every other part of the codebase
that still reads the field directly keeps showing the right "current"
value.
"""
import calendar
import datetime
import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

logger = logging.getLogger('attendance')


def _validate_cycle_day(cycle_start_day):
    try:
        day = int(cycle_start_day)
    except (TypeError, ValueError):
        raise ValidationError('Cycle start day must be a whole number.')
    if not (1 <= day <= 28):
        raise ValidationError('Cycle start day must be between 1 and 28.')
    return day


def _validate_date(effective_date):
    if isinstance(effective_date, datetime.datetime):
        return effective_date.date()
    if isinstance(effective_date, datetime.date):
        return effective_date
    try:
        return datetime.date.fromisoformat(str(effective_date))
    except (TypeError, ValueError):
        raise ValidationError('Effective date must be a valid date (YYYY-MM-DD).')


def _person_filter(employee):
    from attendance.models import RemoteEmployee
    if isinstance(employee, RemoteEmployee):
        return {'remote_employee': employee}
    return {'employee': employee}


# ------------------------------------------------------------- timelines

def employee_cycle_timeline(employee):
    """This employee's override history, newest first."""
    from attendance.models import SalaryCycleHistory
    return list(SalaryCycleHistory.objects.filter(**_person_filter(employee))
                .order_by('-effective_date'))


def default_cycle_timeline():
    """The company-wide default history, newest first."""
    from attendance.models import SalaryCycleDefault
    return list(SalaryCycleDefault.objects.order_by('-effective_date'))


def current_default_cycle(as_of=None):
    """The default row actually in effect as of a date (today, unless given).

    Not the same as `default_cycle_timeline()[0]` — that's the row with the
    furthest-future effective date, which may not have started yet. This is
    "what an employee with no override sees right now".
    """
    from attendance.models import SalaryCycleDefault
    today = as_of or datetime.date.today()
    return (SalaryCycleDefault.objects
            .filter(effective_date__lte=today)
            .order_by('-effective_date')
            .first())


def group_cycle_timeline(group_key):
    """One payroll group's override history, newest first."""
    from attendance.models import SalaryCycleGroupDefault
    return list(SalaryCycleGroupDefault.objects.filter(group=group_key).order_by('-effective_date'))


def current_group_cycle(group_key, as_of=None):
    """The group row actually in effect as of a date (today, unless given).

    Same "as of today, not just the newest row" distinction as
    `current_default_cycle()`.
    """
    from attendance.models import SalaryCycleGroupDefault
    today = as_of or datetime.date.today()
    return (SalaryCycleGroupDefault.objects
            .filter(group=group_key, effective_date__lte=today)
            .order_by('-effective_date')
            .first())


def _apply_tier(lower_points, higher_points):
    """Layer a higher-priority tier's points on top of a lower tier's.

    If `higher_points` is non-empty, `lower_points` is truncated to only
    what falls strictly before the higher tier's earliest date, then the
    higher tier's points are appended — i.e. once the higher tier starts,
    it governs exclusively from there on, the lower tier is never
    consulted again even if it changes again later. Both tiers are lists of
    (effective_date, cycle_start_day) sorted ascending.
    """
    if not higher_points:
        return lower_points
    cutoff = higher_points[0][0]
    return [p for p in lower_points if p[0] < cutoff] + higher_points


def _merged_timeline(employee, history_rows=None, default_rows=None, group_rows=None):
    """This employee's full governing timeline, ascending by date.

    A list of (effective_date_or_None, cycle_start_day). The first entry
    always has effective_date=None ("always", the legacy field fallback).

    Three tiers, each masking the one below it from its own earliest entry
    onward (see `_apply_tier`): company default < this employee's payroll
    group (`SalaryCycleGroupDefault`, via
    `payroll.services_payroll_engine.classify_employee_section`) < this
    employee's own override (`SalaryCycleHistory`). A group or an override
    means "this population/person is on their own schedule now", not
    "temporarily deviate then rejoin the broader tier".

    `history_rows`/`default_rows`/`group_rows` let bulk callers pass
    pre-fetched lists instead of hitting the DB per employee. Pass
    `group_rows=[]` explicitly to skip classification entirely (e.g. when
    the caller already knows this employee has no group).
    """
    from attendance.models import SalaryCycleDefault, SalaryCycleGroupDefault, SalaryCycleHistory

    if history_rows is None:
        history_rows = list(SalaryCycleHistory.objects.filter(**_person_filter(employee))
                             .order_by('effective_date'))
    else:
        history_rows = sorted(history_rows, key=lambda r: r.effective_date)

    if default_rows is None:
        default_rows = list(SalaryCycleDefault.objects.order_by('effective_date'))
    else:
        default_rows = sorted(default_rows, key=lambda r: r.effective_date)

    if group_rows is None:
        from payroll.services_payroll_engine import classify_employee_section
        group_key = classify_employee_section(employee)
        group_rows = (list(SalaryCycleGroupDefault.objects.filter(group=group_key).order_by('effective_date'))
                      if group_key else [])
    else:
        group_rows = sorted(group_rows, key=lambda r: r.effective_date)

    genesis = (None, employee.salary_cycle_start_day or 21)

    company_points = [(d.effective_date, d.cycle_start_day) for d in default_rows]
    group_points = [(g.effective_date, g.cycle_start_day) for g in group_rows]
    override_points = [(h.effective_date, h.cycle_start_day) for h in history_rows]

    points = _apply_tier(company_points, group_points)
    points = _apply_tier(points, override_points)

    return [genesis] + points


def _segment_index_at(timeline, ref_date):
    """Index of the timeline entry governing `ref_date` — the latest entry
    with effective_date None or <= ref_date."""
    idx = 0
    for i, (eff_date, _day) in enumerate(timeline):
        if eff_date is None or eff_date <= ref_date:
            idx = i
        else:
            break
    return idx


# ---------------------------------------------------------------- resolve

def resolve_cycle_start_day(employee, year, month):
    """The cycle_start_day whose *shape* governs calendar month (year, month).

    Note this is the shape used to compute the natural end of that month's
    period — the actual period may be shorter or longer once clipped by a
    nearby transition. Use `get_employee_pay_period()` for the real period.
    """
    timeline = _merged_timeline(employee)
    last_day = calendar.monthrange(year, month)[1]
    ref_date = datetime.date(year, month, last_day)
    return timeline[_segment_index_at(timeline, ref_date)][1]


def _natural_period_end(timeline, year, month):
    """The (possibly-clipped) period END for calendar bucket (year, month),
    and the cycle_start_day whose shape produced it.

    Independent per month — no gaps/overlaps on the end side by
    construction: it only ever looks one segment ahead.
    """
    from payroll.views import _get_employee_pay_period

    last_day = calendar.monthrange(year, month)[1]
    ref_date = datetime.date(year, month, last_day)
    idx = _segment_index_at(timeline, ref_date)
    cycle_start_day = timeline[idx][1]

    _start, natural_end, _days, _hol = _get_employee_pay_period(cycle_start_day, year, month)

    if idx + 1 < len(timeline):
        next_start = timeline[idx + 1][0]
        if next_start is not None and next_start <= natural_end:
            natural_end = next_start - datetime.timedelta(days=1)

    return natural_end, cycle_start_day


def get_employee_pay_period(employee, year, month, timeline=None):
    """The exact pay period for this employee for calendar month (year, month).

    Guarantees continuity across a cycle change: this period always starts
    the day immediately after the previous calendar month's period ended,
    and ends either at the natural shape's end or right before the next
    scheduled change — so a day is never double-counted and never skipped,
    regardless of what day of the month a change happens to land on.
    """
    from payroll.views import _count_holidays_in_period

    if timeline is None:
        timeline = _merged_timeline(employee)

    period_end, _cycle_start_day = _natural_period_end(timeline, year, month)

    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    prev_end, _ = _natural_period_end(timeline, prev_year, prev_month)
    period_start = prev_end + datetime.timedelta(days=1)

    days_in_period = (period_end - period_start).days + 1
    total_holidays = _count_holidays_in_period(period_start, period_end)
    return period_start, period_end, days_in_period, total_holidays


def get_employee_pay_periods_bulk(employees, year, month):
    """Same as `get_employee_pay_period`, batched for a whole payroll run.

    Returns {employee.pk: (period_start, period_end, days, holidays)}. A
    fixed small number of queries (company default + all group defaults +
    override history for the requested employees + the group-classification
    lookup) regardless of how many employees are passed.
    """
    from attendance.models import RemoteEmployee, SalaryCycleDefault, SalaryCycleGroupDefault, SalaryCycleHistory
    from payroll.services_payroll_engine import classify_employees_bulk

    employees = list(employees)
    if not employees:
        return {}

    inhouse = [e for e in employees if not isinstance(e, RemoteEmployee)]
    remote = [e for e in employees if isinstance(e, RemoteEmployee)]

    default_rows = list(SalaryCycleDefault.objects.order_by('effective_date'))

    group_rows_by_key = {}
    for row in SalaryCycleGroupDefault.objects.order_by('effective_date'):
        group_rows_by_key.setdefault(row.group, []).append(row)

    group_by_employee = classify_employees_bulk(employees)

    history_by_employee = {}
    for bucket, filter_field in ((inhouse, 'employee_id'), (remote, 'remote_employee_id')):
        if not bucket:
            continue
        ids = [e.pk for e in bucket]
        rows = SalaryCycleHistory.objects.filter(**{f'{filter_field}__in': ids}).order_by('effective_date')
        for row in rows:
            key = getattr(row, filter_field)
            history_by_employee.setdefault(key, []).append(row)

    result = {}
    for emp in employees:
        group_key = group_by_employee.get(emp.pk)
        timeline = _merged_timeline(
            emp,
            history_rows=history_by_employee.get(emp.pk, []),
            default_rows=default_rows,
            group_rows=group_rows_by_key.get(group_key, []) if group_key else [],
        )
        result[emp.pk] = get_employee_pay_period(emp, year, month, timeline=timeline)
    return result


# ----------------------------------------------------------- lock check

def _is_locked_period(employee, effective_date):
    """True if this employee already has a paid or frozen record for the
    calendar month this date falls in, or any month after it."""
    from payroll.models import FrozenPayrollMonth, PaidSalaryRecord

    year, month = effective_date.year, effective_date.month
    at_or_after = Q(year__gt=year) | Q(year=year, month__gte=month)
    if FrozenPayrollMonth.objects.filter(at_or_after).exists():
        return True
    return PaidSalaryRecord.objects.filter(at_or_after, **_person_filter(employee)).exists()


def _is_locked_default_period(effective_date):
    from payroll.models import FrozenPayrollMonth, PaidSalaryRecord

    year, month = effective_date.year, effective_date.month
    at_or_after = Q(year__gt=year) | Q(year=year, month__gte=month)
    if FrozenPayrollMonth.objects.filter(at_or_after).exists():
        return True
    return PaidSalaryRecord.objects.filter(at_or_after).exists()


# --------------------------------------------------------------- mutate

@transaction.atomic
def set_employee_cycle_override(employee, cycle_start_day, effective_date, actor='', note='',
                                 allow_noop_skip=True):
    """Create or correct one dated override row for this employee.

    Re-submitting the same (employee, effective_date) corrects that entry
    rather than erroring. If a paid or frozen record already exists for the
    affected month(s), this never blocks the save — only future
    recalculation is affected, the locked snapshot itself is never touched
    — it just attaches a `.warning` string to the returned row.

    `allow_noop_skip` (default True): when the submitted day already matches
    what would resolve for that date anyway (nothing would actually change),
    skip creating a row rather than clutter the timeline. This is meant for
    write paths where the pay cycle is only *one of several* incidental
    fields being saved (e.g. the main employee-edit form, which always
    resends the currently-shown value). Callers backing a dedicated "add a
    pay-cycle entry" action — where the user explicitly asked for a row to
    be recorded — should pass `allow_noop_skip=False`, since silently
    returning None there looks like a bug: the request reports success but
    nothing appears in the history table.
    """
    from attendance.audit import log_audit
    from attendance.models import SalaryCycleHistory

    day = _validate_cycle_day(cycle_start_day)
    eff_date = _validate_date(effective_date)

    existing_row = SalaryCycleHistory.objects.filter(
        effective_date=eff_date, **_person_filter(employee)).first()
    if (allow_noop_skip and existing_row is None and not (note or '').strip()
            and day == resolve_cycle_start_day(employee, eff_date.year, eff_date.month)):
        # Nothing would actually change — e.g. a form resubmitted its current
        # value untouched. Recording a no-op row would clutter the timeline
        # for a save that never intended to touch the pay cycle at all.
        return None

    warning = None
    if _is_locked_period(employee, eff_date):
        warning = (
            'A payroll record already exists on or after %s — this '
            'employee\'s pay will only be recalculated with the new cycle '
            'for months not yet paid.' % eff_date)

    defaults = {'cycle_start_day': day, 'note': (note or '').strip(), 'created_by': actor or ''}
    obj, created = SalaryCycleHistory.objects.update_or_create(
        effective_date=eff_date, **_person_filter(employee),
        defaults=defaults,
    )
    obj.full_clean(exclude=['employee', 'remote_employee'])
    obj.save()
    obj.warning = warning

    latest = employee_cycle_timeline(employee)
    if latest and latest[0].pk == obj.pk:
        employee.salary_cycle_start_day = day
        employee.save(update_fields=['salary_cycle_start_day'])

    try:
        from attendance.models import AuditLog
        log_audit(actor=actor or 'system',
                  action=AuditLog.ACTION_CREATE if created else AuditLog.ACTION_UPDATE,
                  instance=obj, note='pay cycle day=%s effective %s' % (day, eff_date))
    except Exception:                                             # noqa: BLE001
        logger.exception('salary cycle audit failed for %s', employee)

    return obj


@transaction.atomic
def set_default_cycle(cycle_start_day, effective_date, actor='', note=''):
    """Create or correct one dated row on the company-wide default timeline.

    Never blocks on an already-paid/frozen month — attaches a `.warning`
    string to the returned row instead, same as `set_employee_cycle_override`.
    """
    from attendance.audit import log_audit
    from attendance.models import SalaryCycleDefault

    day = _validate_cycle_day(cycle_start_day)
    eff_date = _validate_date(effective_date)

    warning = None
    if _is_locked_default_period(eff_date):
        warning = (
            'Payroll records already exist on or after %s for some '
            'employees — only months not yet paid will be recalculated '
            'with the new default.' % eff_date)

    defaults = {'cycle_start_day': day, 'note': (note or '').strip(), 'created_by': actor or ''}
    obj, created = SalaryCycleDefault.objects.update_or_create(
        effective_date=eff_date, defaults=defaults,
    )
    obj.full_clean()
    obj.save()
    obj.warning = warning

    try:
        from attendance.models import AuditLog
        log_audit(actor=actor or 'system',
                  action=AuditLog.ACTION_CREATE if created else AuditLog.ACTION_UPDATE,
                  instance=obj, note='default pay cycle day=%s effective %s' % (day, eff_date))
    except Exception:                                             # noqa: BLE001
        logger.exception('salary cycle default audit failed')

    return obj


@transaction.atomic
def set_group_cycle(group_key, cycle_start_day, effective_date, actor='', note=''):
    """Create or correct one dated row on one payroll group's timeline.

    Same shape as `set_default_cycle()` — upsert by `(group, effective_date)`,
    never blocks on an already-paid/frozen month (attaches `.warning`
    instead). `group_key` must be one of `SalaryCycleGroupDefault.GROUP_CHOICES`.
    """
    from attendance.audit import log_audit
    from attendance.models import SalaryCycleGroupDefault

    valid_groups = dict(SalaryCycleGroupDefault.GROUP_CHOICES)
    if group_key not in valid_groups:
        raise ValidationError('Unknown payroll group: %s' % group_key)

    day = _validate_cycle_day(cycle_start_day)
    eff_date = _validate_date(effective_date)

    warning = None
    if _is_locked_default_period(eff_date):
        warning = (
            'Payroll records already exist on or after %s for some '
            'employees in this group — only months not yet paid will be '
            'recalculated with the new cycle.' % eff_date)

    defaults = {'cycle_start_day': day, 'note': (note or '').strip(), 'created_by': actor or ''}
    obj, created = SalaryCycleGroupDefault.objects.update_or_create(
        group=group_key, effective_date=eff_date, defaults=defaults,
    )
    obj.full_clean()
    obj.save()
    obj.warning = warning

    try:
        from attendance.models import AuditLog
        log_audit(actor=actor or 'system',
                  action=AuditLog.ACTION_CREATE if created else AuditLog.ACTION_UPDATE,
                  instance=obj, note='group pay cycle day=%s effective %s' % (day, eff_date))
    except Exception:                                             # noqa: BLE001
        logger.exception('salary cycle group audit failed for %s', group_key)

    return obj


@transaction.atomic
def delete_latest_group_default(group_key, entry_id, actor=''):
    """Undo the most recent entry for one payroll group. Refuses anything older."""
    from attendance.audit import log_audit
    from attendance.models import SalaryCycleGroupDefault

    timeline = group_cycle_timeline(group_key)
    if not timeline or timeline[0].pk != int(entry_id):
        raise ValidationError(
            'Only the most recent entry can be removed — correct an older '
            'entry by adding a new one instead of rewriting history.')

    row = timeline[0]
    row.delete()

    try:
        from attendance.models import AuditLog
        log_audit(actor=actor or 'system', action=AuditLog.ACTION_DELETE,
                  instance=SalaryCycleGroupDefault(id=entry_id, group=group_key),
                  note='removed group pay cycle effective %s' % row.effective_date)
    except Exception:                                             # noqa: BLE001
        logger.exception('salary cycle group delete audit failed for %s', group_key)


@transaction.atomic
def delete_latest_employee_override(employee, history_id, actor=''):
    """Undo the most recent override for this employee. Refuses anything older."""
    from attendance.audit import log_audit

    timeline = employee_cycle_timeline(employee)
    if not timeline or timeline[0].pk != int(history_id):
        raise ValidationError(
            'Only the most recent entry can be removed — correct an older '
            'entry by adding a new one instead of rewriting history.')

    row = timeline[0]
    row.delete()

    remaining = employee_cycle_timeline(employee)
    if remaining:
        employee.salary_cycle_start_day = remaining[0].cycle_start_day
        employee.save(update_fields=['salary_cycle_start_day'])
    # If nothing is left, the legacy field value stands as-is — it already
    # reflects whatever was last explicitly set before history existed.

    try:
        from attendance.models import AuditLog
        log_audit(actor=actor or 'system', action=AuditLog.ACTION_DELETE,
                  instance=employee, note='removed pay cycle override effective %s' % row.effective_date)
    except Exception:                                             # noqa: BLE001
        logger.exception('salary cycle delete audit failed for %s', employee)


@transaction.atomic
def delete_latest_default(default_id, actor=''):
    """Undo the most recent company-wide default entry. Refuses anything older."""
    from attendance.audit import log_audit
    from attendance.models import SalaryCycleDefault

    timeline = default_cycle_timeline()
    if not timeline or timeline[0].pk != int(default_id):
        raise ValidationError(
            'Only the most recent entry can be removed — correct an older '
            'entry by adding a new one instead of rewriting history.')

    row = timeline[0]
    row.delete()

    try:
        from attendance.models import AuditLog
        log_audit(actor=actor or 'system', action=AuditLog.ACTION_DELETE,
                  instance=SalaryCycleDefault(id=default_id),
                  note='removed default pay cycle effective %s' % row.effective_date)
    except Exception:                                             # noqa: BLE001
        logger.exception('salary cycle default delete audit failed')
