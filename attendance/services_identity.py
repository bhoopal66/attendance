"""Identity and compliance records — visas, cover, credentials.

Two jobs:

    renew_visa()          keep the old visa instead of overwriting it
    expiring_identity()   surface the expiries that live OUTSIDE EmployeeDocument

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not touch `services_compliance.watchlist()`. That reads expiry from
`EmployeeDocument` and is what the compliance dashboard alerts on today.
Repointing it at these tables is a real change to a live alerting surface and
belongs in its own pass with its own before/after.

Meanwhile visa, insurance, medical and professional-licence expiries have no
alerting at all — they are new. `expiring_identity()` is what covers them until
the two are merged. It is additive: it reports on tables the watchlist has never
read, so nothing is alerted twice.
"""
import datetime
import logging

from django.core.exceptions import ValidationError
from django.db import transaction

logger = logging.getLogger('attendance')


def _person_filter(person):
    from attendance.models import Employee
    if isinstance(person, Employee):
        return {'employee': person, 'remote_employee': None}
    return {'employee': None, 'remote_employee': person}


def current_visa(person):
    from attendance.models import EmployeeVisa
    return (EmployeeVisa.objects.filter(**_person_filter(person), is_current=True)
            .order_by('-issue_date', '-id').first())


def visa_history(person):
    from attendance.models import EmployeeVisa
    return EmployeeVisa.objects.filter(**_person_filter(person)).order_by('-issue_date', '-id')


@transaction.atomic
def renew_visa(person, actor='', supersede_status='expired', **fields):
    """Record a new visa and retire the previous one. Returns the new row.

    The previous visa is kept, marked not-current, and given a closing status.
    It is NOT deleted and NOT edited beyond those two flags: its permit number
    and file number are what a government query months later is made against.
    """
    from attendance.models import EmployeeVisa
    from attendance import services_timeline as timeline

    if supersede_status not in dict(EmployeeVisa.VISA_STATUS_CHOICES):
        raise ValidationError('Unknown closing status "%s".' % supersede_status)

    previous = current_visa(person)
    new = EmployeeVisa(**_person_filter(person), is_current=True,
                       status='active', created_by=actor or '', **fields)
    new.full_clean(exclude=['employee', 'remote_employee', 'document'])

    if previous:
        previous.is_current = False
        if previous.status == 'active':
            previous.status = supersede_status
        previous.save(update_fields=['is_current', 'status', 'updated_at'])
    new.save()

    timeline.record(person, new.issue_date or datetime.date.today(),
                    'compliance', 'visa_renewed',
                    'Visa renewed' if previous else 'Visa issued',
                    detail=(f'{new.residence_permit_number or new.visa_file_number or ""} '
                            f'expires {new.expiry_date or "unknown"}').strip(),
                    source_model='EmployeeVisa', source_id=new.pk, actor=actor)
    return new


@transaction.atomic
def cancel_visa(person, cancellation_date, reference='', actor=''):
    """Cancel the current visa — the leaver path. Nothing is deleted."""
    from attendance import services_timeline as timeline
    visa = current_visa(person)
    if visa is None:
        raise ValidationError('There is no current visa to cancel.')
    visa.status = 'cancelled'
    visa.is_current = False
    visa.cancellation_date = cancellation_date
    visa.cancellation_reference = reference or ''
    visa.save(update_fields=['status', 'is_current', 'cancellation_date',
                             'cancellation_reference', 'updated_at'])
    timeline.record(person, cancellation_date, 'compliance', 'visa_cancelled',
                    'Visa cancelled', detail=reference or '',
                    source_model='EmployeeVisa', source_id=visa.pk, actor=actor)
    return visa


# Expiry tiers mirror services_compliance.EXPIRY_TIERS so the two surfaces
# describe urgency the same way. Missing is NOT valid — an insurance policy with
# no end date is not "never expiring", it is unrecorded, and it is reported as
# such rather than passed over.
TIERS = [(30, 'tier30'), (60, 'tier60'), (90, 'tier90')]


def _tier(expiry, today):
    if expiry is None:
        return 'missing', None
    days = (expiry - today).days
    if days < 0:
        return 'expired', days
    for limit, name in TIERS:
        if days <= limit:
            return name, days
    return 'ok', days


def expiring_identity(person, today=None):
    """Every dated identity record and how close it is to lapsing.

    Covers the tables EmployeeDocument does not: visa, insurance, medical
    fitness and professional licences.
    """
    from attendance.models import (
        EmployeeInsurance, EmployeeMedicalFitness, EmployeeQualification,
    )
    today = today or datetime.date.today()
    pf = _person_filter(person)
    rows = []

    visa = current_visa(person)
    if visa:
        tier, days = _tier(visa.expiry_date, today)
        rows.append({'kind': 'visa', 'label': 'Residence visa',
                     'reference': visa.residence_permit_number or visa.visa_file_number or '',
                     'expiry': visa.expiry_date, 'tier': tier, 'days': days,
                     'source_id': visa.pk})

    for ins in EmployeeInsurance.objects.filter(**pf, is_current=True):
        tier, days = _tier(ins.coverage_end, today)
        rows.append({'kind': 'insurance', 'label': f'Insurance — {ins.provider}',
                     'reference': ins.policy_number, 'expiry': ins.coverage_end,
                     'tier': tier, 'days': days, 'source_id': ins.pk})

    med = EmployeeMedicalFitness.objects.filter(**pf).order_by('-test_date', '-id').first()
    if med and med.expiry_date:
        tier, days = _tier(med.expiry_date, today)
        rows.append({'kind': 'medical', 'label': 'Medical fitness',
                     'reference': med.certificate_number, 'expiry': med.expiry_date,
                     'tier': tier, 'days': days, 'source_id': med.pk})

    for q in EmployeeQualification.objects.filter(**pf, is_current=True):
        if q.expiry_date:
            tier, days = _tier(q.expiry_date, today)
            rows.append({'kind': 'qualification', 'label': q.title,
                         'reference': q.membership_number, 'expiry': q.expiry_date,
                         'tier': tier, 'days': days, 'source_id': q.pk})

    order = {'expired': 0, 'tier30': 1, 'tier60': 2, 'tier90': 3, 'missing': 4, 'ok': 5}
    rows.sort(key=lambda r: (order[r['tier']], r['expiry'] or datetime.date.max))
    return rows


VISA_NUMBER_FIELDS = [
    ('uid_number', 'UID number'),
    ('visa_file_number', 'Visa file number'),
    ('residence_permit_number', 'Residence permit number'),
]


def visa_masked_fields(visa, role):
    """The current visa's identity numbers, masked per the same rules as
    Emirates ID/passport — reveal goes through the existing
    compliance_reveal endpoint with group='visa_number'."""
    from . import compliance_access as access

    if visa is None:
        return []
    out = []
    for key, label in VISA_NUMBER_FIELDS:
        payload = access.field_payload(role, 'visa_number', getattr(visa, key))
        if payload is None:
            continue
        row = {'key': key, 'label': label}
        row.update(payload)
        out.append(row)
    return out


def identity_summary(person, today=None):
    """One dict for the profile — counts, the current visa, and what needs doing."""
    from attendance.models import (
        EmployeeDependent, EmployeeEducation, EmployeePreviousEmployment,
        EmployeeQualification,
    )
    today = today or datetime.date.today()
    pf = _person_filter(person)
    items = expiring_identity(person, today)
    needs_action = [r for r in items if r['tier'] in ('expired', 'tier30', 'tier60', 'missing')]
    return {
        'visa': current_visa(person),
        'visa_history_count': visa_history(person).count(),
        'dependents': EmployeeDependent.objects.filter(**pf).count(),
        'sponsored_dependents': EmployeeDependent.objects.filter(
            **pf, sponsored_by_company=True).count(),
        'education': EmployeeEducation.objects.filter(**pf).count(),
        'qualifications': EmployeeQualification.objects.filter(**pf).count(),
        'previous_employers': EmployeePreviousEmployment.objects.filter(**pf).count(),
        'references_outstanding': EmployeePreviousEmployment.objects.filter(
            **pf, reference_checked=False).count(),
        'expiring': items,
        'needs_action': needs_action,
    }
