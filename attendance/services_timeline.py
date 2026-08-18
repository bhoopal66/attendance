"""Writing and reading the employee timeline.

`record()` is safe to call twice. Every event carries a dedupe key built from
what makes it unique, so a backfill re-run, a retried request or two code paths
firing on the same change all produce ONE line. A timeline that double-counts a
promotion is worse than no timeline, because nobody doubts it.
"""
import datetime
import logging

logger = logging.getLogger('attendance')


def _kind_and_id(employee):
    from attendance.models import Employee
    if isinstance(employee, Employee):
        return 'inhouse', employee.pk, {'employee': employee, 'remote_employee': None}
    return 'remote', employee.pk, {'employee': None, 'remote_employee': employee}


def make_key(employee, event_type, event_date, source_model='', source_id=''):
    kind, pk, _ = _kind_and_id(employee)
    return f'{kind}:{pk}:{event_type}:{event_date}:{source_model}:{source_id}'[:190]


def record(employee, event_date, category, event_type, title, detail='',
           source_model='', source_id='', actor=''):
    """Write one event, or return the one already there. Never raises.

    Never raises deliberately. A timeline entry is a description of something
    that already happened; if writing the description fails, the thing itself
    must not be rolled back. It is logged instead.
    """
    from attendance.models import EmployeeTimelineEvent
    try:
        if isinstance(event_date, datetime.datetime):
            event_date = event_date.date()
        _, _, person = _kind_and_id(employee)
        key = make_key(employee, event_type, event_date, source_model, str(source_id or ''))
        obj, _created = EmployeeTimelineEvent.objects.get_or_create(
            dedupe_key=key,
            defaults=dict(event_date=event_date, category=category,
                          event_type=event_type, title=title, detail=detail or '',
                          source_model=source_model or '', source_id=str(source_id or ''),
                          created_by=actor or '', **person))
        return obj
    except Exception:                                           # noqa: BLE001
        logger.exception('timeline write failed for %s / %s', employee, event_type)
        return None


def timeline(employee, categories=None, since=None, until=None):
    """The chronology, newest first, optionally filtered (§46)."""
    from attendance.models import EmployeeTimelineEvent
    _, _, person = _kind_and_id(employee)
    qs = EmployeeTimelineEvent.objects.filter(**person)
    if categories:
        qs = qs.filter(category__in=list(categories))
    if since:
        qs = qs.filter(event_date__gte=since)
    if until:
        qs = qs.filter(event_date__lte=until)
    return qs.order_by('-event_date', '-id')
