"""The leave ledger — posting movements and reading a balance (§30).

    post()          add one movement, with the balance it left behind
    balance()       the balance as at a date
    statement()     the rows, in order, that add up to it
    reconcile()     ledger balance vs the computed one, and the gap

WHY RECONCILE EXISTS
--------------------
`services_leave_earnings.leave_summary()` computes a balance as
`accrued − taken`, live, from the leave tables. This ledger stores movements.
Introducing a second way to know the same number is how two screens start
disagreeing and nobody can say which is right.

So the ledger does NOT replace the computed figure yet. `reconcile()` puts them
side by side and reports the difference per employee. The switchover happens
when that difference is zero for everybody and somebody has looked at why for
the ones where it is not — not on the day the table is created.

SIGN CONVENTION
---------------
One signed `days` column. Positive credits, negative consumes. Debit/credit
pairs invite a row with both filled in, and nothing catches it.
"""
import datetime
import logging
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

logger = logging.getLogger('attendance')

MANUAL_KINDS = {'adjustment', 'opening', 'expiry', 'reversal'}


def _person_filter(person):
    from attendance.models import Employee
    if isinstance(person, Employee):
        return {'employee': person, 'remote_employee': None}
    return {'employee': None, 'remote_employee': person}


def _kind_and_id(person):
    from attendance.models import Employee
    return ('inhouse' if isinstance(person, Employee) else 'remote'), person.pk


def _d(value):
    return Decimal(str(value or 0))


def statement(person, leave_type_code='annual', until=None):
    from attendance.models import LeaveLedgerEntry
    qs = LeaveLedgerEntry.objects.filter(**_person_filter(person),
                                         leave_type_code=leave_type_code)
    if until:
        qs = qs.filter(entry_date__lte=until)
    return qs.order_by('entry_date', 'id')


def balance(person, leave_type_code='annual', as_of=None):
    """The balance as at a date. Decimal('0') when there is no ledger at all.

    Zero is correct here, unlike elsewhere: an empty ledger is not an unknown
    balance, it is a balance that has had nothing posted to it.
    """
    last = statement(person, leave_type_code, until=as_of).last()
    return last.balance_after if last else Decimal('0.00')


@transaction.atomic
def post(person, entry_date, kind, days, description='', reason='',
         leave_type_code='annual', source_model='', source_id='',
         actor='', approved_by=''):
    """Add one movement. Returns the entry, or the existing one if already posted.

    Refuses a manual adjustment with no reason. An unexplained balance change is
    the first thing an audit goes looking for, and "the system let me" is not an
    answer anybody wants to give.

    Refuses to post BEHIND the last entry. A ledger is only trustworthy in
    order: inserting a movement before rows whose `balance_after` is already
    written would leave every later row stating a balance that never existed.
    Corrections go on the end, as a dated adjustment — which is what a
    correction is.
    """
    from attendance.models import LeaveLedgerEntry

    if isinstance(entry_date, datetime.datetime):
        entry_date = entry_date.date()
    if kind not in dict(LeaveLedgerEntry.KIND_CHOICES):
        raise ValidationError('Unknown ledger entry kind "%s".' % kind)
    if kind in MANUAL_KINDS and not (reason or '').strip():
        raise ValidationError(
            "A %s needs a reason. An unexplained change to somebody's leave "
            'balance is not auditable.' % kind)

    ptype, pid = _kind_and_id(person)
    key = f'{ptype}:{pid}:{leave_type_code}:{kind}:{entry_date}:{source_model}:{source_id}:{days}'[:190]
    existing = LeaveLedgerEntry.objects.filter(dedupe_key=key).first()
    if existing:
        return existing

    last = statement(person, leave_type_code).last()
    if last and entry_date < last.entry_date:
        raise ValidationError(
            'Cannot post %s behind the last entry (%s). Every later row already '
            'states a balance; post a dated adjustment instead.'
            % (entry_date, last.entry_date))

    running = (last.balance_after if last else Decimal('0.00')) + _d(days)
    entry = LeaveLedgerEntry(
        entry_date=entry_date, kind=kind, days=_d(days), balance_after=running,
        description=description or '', reason=reason or '',
        leave_type_code=leave_type_code, source_model=source_model or '',
        source_id=str(source_id or ''), dedupe_key=key,
        created_by=actor or '', approved_by=approved_by or '',
        **_person_filter(person))
    entry.full_clean(exclude=['employee', 'remote_employee'])
    entry.save()
    return entry


def policy_version_for(person, as_of=None, leave_type_code='annual'):
    """The rule that applied to this person on this date, or None.

    Most specific first: jurisdiction and company both matching beats
    jurisdiction alone, which beats the catch-all. Returning None is meaningful —
    it means nobody has written a policy that covers this person, and the caller
    should say so rather than fall back to a number in the code.
    """
    from attendance.models import LeavePolicy, LeavePolicyVersion
    from django.db.models import Q

    as_of = as_of or datetime.date.today()
    juris = getattr(person, 'labour_jurisdiction', '') or ''
    company = getattr(person, 'company', None)

    candidates = LeavePolicy.objects.filter(is_active=True,
                                            leave_type_code=leave_type_code)
    ranked = []
    for pol in candidates:
        if pol.labour_jurisdiction and pol.labour_jurisdiction != juris:
            continue
        if pol.company_id and company and pol.company_id != company.pk:
            continue
        if pol.company_id and not company:
            continue
        score = (1 if pol.labour_jurisdiction else 0) + (1 if pol.company_id else 0)
        ranked.append((score, pol.pk, pol))
    if not ranked:
        return None
    ranked.sort(key=lambda t: (-t[0], t[1]))
    for _score, _pk, pol in ranked:
        version = (LeavePolicyVersion.objects.filter(policy=pol, effective_from__lte=as_of)
                   .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=as_of))
                   .order_by('-effective_from').first())
        if version:
            return version
    return None


def reconcile(person, as_of=None, leave_type_code='annual'):
    """Ledger balance against the computed one. Reports the gap, resolves nothing."""
    from payroll import services_leave_earnings as earnings

    as_of = as_of or datetime.date.today()
    ledger = balance(person, leave_type_code, as_of)
    summary = earnings.leave_summary(person, as_of)
    computed = summary.get('balance_days')
    computed_dec = None if computed is None else _d(computed)
    return {
        'person': person,
        'ledger_balance': ledger,
        'computed_balance': computed_dec,
        'difference': None if computed_dec is None else (ledger - computed_dec),
        'agrees': computed_dec is not None and abs(ledger - computed_dec) < Decimal('0.05'),
        'entries': statement(person, leave_type_code, as_of).count(),
        'policy_version': policy_version_for(person, as_of, leave_type_code),
    }
