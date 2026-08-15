"""
Paid Holidays - declaring a month's holidays and paying for them.

THE ARITHMETIC, AND WHY IT IS THAT WAY
--------------------------------------
    days   = declared dates, minus Sundays
    daily  = the employee's gross for the month / days in their pay period
    amount = daily x days

Both halves are deliberate and they belong together:

*Sundays are excluded from the count* because a Sunday is already non-working
and is never deducted for. Paying one would credit a day nobody was going to
lose.

*The divisor is the full pay period* (~30-31 days, Sundays included), not the
working days, because that is exactly the divisor `payroll/views.py` uses when
it deducts for absence. Using working days here would make a day paid larger
than a day deducted, and no one could explain why.

The result: a day paid and a day deducted are the same size. That is a
sentence you can say to an employee.

WHERE THE GROSS COMES FROM
--------------------------
`payroll.services_payroll_engine` - the Phase 1 seam. This is its first real
consumer beyond the regression harness, and it is a READ: nothing here changes
how payroll is calculated. Rows the engine marks `has_salary_structure=False`
(pure-commission staff with no salary concept) are skipped with a reason
rather than paid from a fabricated rate.

WHAT CONFIRMING DOES
--------------------
Writes one ordinary `DeductionEntry` per award, category `paid_holiday`,
`split_months=1`, dated to the declaration's month. Payroll then picks them up
through the path it already uses for every manual addition. No calculation
code is touched.
"""

import datetime
import logging
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from attendance.audit import log_audit
from attendance.models import AuditLog, Holiday

from .models import (
    DeductionEntry, DeductionType, PaidHolidayAward, PaidHolidayDeclaration,
)

logger = logging.getLogger('payroll')

CENT = Decimal('0.01')
ZERO = Decimal('0.00')


# ------------------------------------------------------------------ helpers

def declaration_for(year, month, create=False, actor=''):
    obj = PaidHolidayDeclaration.objects.filter(year=year, month=month).first()
    if obj is None and create:
        obj = PaidHolidayDeclaration.objects.create(
            year=year, month=month, dates=[], created_by=actor)
    return obj


def is_confirmed(year, month):
    d = declaration_for(year, month)
    return bool(d and d.status == PaidHolidayDeclaration.STATUS_CONFIRMED)


def pay_period(year, month):
    """The default 21st-20th window a declaration for this month lives in.

    Every declared date must fall inside it. Nothing else defines "this
    month" for payroll purposes, so nothing else may define it here.
    """
    from .services_payroll_engine import get_pay_period

    class _Default:
        salary_cycle_start_day = 21
    return get_pay_period(_Default(), year, month)


def out_of_period(year, month, dates):
    """Declared dates that do not belong to this month's pay period.

    THIS EXISTS BECAUSE A DATE IN AUGUST WAS ONCE SAVED AS THE JANUARY
    DECLARATION. Nothing rejected it, so January paid a holiday that had not
    happened yet. A date outside the period is a typo, never an instruction:
    the fix is to refuse it, not to pay it.
    """
    period = pay_period(year, month)
    bad = []
    for d in dates or []:
        try:
            dt = datetime.date.fromisoformat(d) if isinstance(d, str) else d
        except (TypeError, ValueError):
            bad.append({'date': str(d), 'reason': 'not a valid date'})
            continue
        if not (period.start <= dt <= period.end):
            bad.append({'date': dt.isoformat(),
                        'reason': f'outside the pay period {period.start} to {period.end}'})
    return bad


def suggested_dates(year, month):
    """Public holidays already recorded inside this month's default pay period.

    A suggestion only - the operator decides what is actually paid. Read from
    the same `Holiday` table the attendance calculation uses, so the two cannot
    disagree about which days are holidays.
    """
    period = pay_period(year, month)
    rows = Holiday.objects.filter(date__gte=period.start, date__lte=period.end).order_by('date')
    return [{'date': h.date.isoformat(), 'name': h.name,
             'is_sunday': h.date.weekday() == 6} for h in rows]


def _paid_days(dates):
    out = []
    for d in dates or []:
        try:
            dt = datetime.date.fromisoformat(d) if isinstance(d, str) else d
        except (TypeError, ValueError):
            continue
        if dt.weekday() != 6:
            out.append(dt)
    return sorted(out)


# ------------------------------------------------------------------ preview

def build_awards(year, month, dates):
    """What every employee would receive. Computes only - writes nothing."""
    from .services_payroll_engine import (
        SECTION_SALES_PERF_METHOD2, build_all_sections, get_pay_period,
    )

    day_count = len(_paid_days(dates))
    sections = build_all_sections(year, month)

    seen = set()
    out = []
    for section, rows in sections.items():
        # Method 2 is a comparison view of people already listed under
        # Sales: Performance - including it would pay them twice.
        if section == SECTION_SALES_PERF_METHOD2:
            continue
        for row in rows:
            emp = row.get('employee')
            if emp is None:
                continue
            emp_type = row.get('employee_type', 'inhouse')
            key = (emp_type, emp.id)
            if key in seen:
                continue
            seen.add(key)

            gross = (Decimal(str(row.get('basic_salary') or 0))
                     + Decimal(str(row.get('housing_allowance') or 0))
                     + Decimal(str(row.get('transport_allowance') or 0))
                     + Decimal(str(row.get('phone_allowance') or 0))
                     + Decimal(str(row.get('other_allowance_amt') or 0)))
            period = get_pay_period(emp, year, month)
            period_days = period.days or 0
            currency = getattr(emp, 'currency', 'AED') or 'AED'

            entry = {
                'employee': emp, 'employee_type': emp_type, 'section': section,
                'name': emp.name, 'tcr': getattr(emp, 'tcr_id', '') or '',
                'currency': currency, 'days': day_count,
                'gross_used': gross.quantize(CENT), 'period_days': period_days,
                'daily_rate': ZERO, 'amount': ZERO,
                'skipped': False, 'skip_reason': '',
            }

            if day_count == 0:
                entry.update(skipped=True, skip_reason='no payable days declared')
            elif not row.get('has_salary_structure', False) or gross <= ZERO:
                # Pure-commission staff have no salary to derive a day from.
                # Inventing one would pay a holiday at a rate nobody agreed.
                entry.update(skipped=True,
                             skip_reason='no gross for this month — nothing to derive a daily rate from')
            elif period_days <= 0:
                entry.update(skipped=True, skip_reason='pay period could not be resolved')
            else:
                daily = (gross / Decimal(period_days)).quantize(CENT, rounding=ROUND_HALF_UP)
                entry['daily_rate'] = daily
                entry['amount'] = (daily * day_count).quantize(CENT, rounding=ROUND_HALF_UP)
                if entry['amount'] <= ZERO:
                    entry.update(skipped=True, skip_reason='computed amount rounds to zero')
            out.append(entry)

    out.sort(key=lambda r: (r['skipped'], r['name'].lower()))
    return out


def totals_by_currency(awards):
    totals = {}
    for a in awards:
        if a['skipped']:
            continue
        totals[a['currency']] = totals.get(a['currency'], ZERO) + a['amount']
    return totals


# ------------------------------------------------------------------ confirm

@transaction.atomic
def confirm(year, month, dates, actor='', note=''):
    """Record the declaration and write the additions.

    Refuses if the month is already confirmed - re-running would pay everyone
    twice. Withdraw first if the dates were wrong.
    """
    existing = declaration_for(year, month)
    if existing and existing.status == PaidHolidayDeclaration.STATUS_CONFIRMED:
        raise ValueError(
            f'Paid holidays for {month:02d}/{year} are already confirmed. '
            'Withdraw that declaration first — confirming again would pay everyone twice.')

    bad = out_of_period(year, month, dates)
    if bad:
        detail = '; '.join(f"{b['date']} ({b['reason']})" for b in bad)
        raise ValueError(
            f'These dates do not belong to {month:02d}/{year}: {detail}. '
            'Nothing was saved.')

    dtype = DeductionType.objects.filter(
        code=PaidHolidayDeclaration.DEDUCTION_CODE).first()
    payable = _paid_days(dates)
    if payable and dtype is None:
        raise ValueError(
            f'The "{PaidHolidayDeclaration.DEDUCTION_CODE}" type does not exist. '
            'Create it on the Deduction Types page first.')
    if payable and dtype is not None and not dtype.is_active:
        raise ValueError(f'{dtype.name} is inactive — reactivate it before paying holidays.')

    decl = existing or PaidHolidayDeclaration(year=year, month=month, created_by=actor)
    decl.dates = [d.isoformat() if hasattr(d, 'isoformat') else str(d) for d in (dates or [])]
    decl.note = note or ''
    decl.status = PaidHolidayDeclaration.STATUS_CONFIRMED
    decl.confirmed_by = actor
    decl.confirmed_at = timezone.now()
    decl.full_clean(exclude=['created_at'])
    decl.save()

    decl.awards.all().delete()   # a draft may have left a stale preview

    created = 0
    for a in build_awards(year, month, decl.dates):
        emp = a['employee']
        fk = ({'employee': emp} if a['employee_type'] == 'inhouse'
              else {'remote_employee': emp})
        award = PaidHolidayAward(
            declaration=decl, days=a['days'], gross_used=a['gross_used'],
            period_days=a['period_days'], daily_rate=a['daily_rate'],
            amount=a['amount'], currency=a['currency'],
            skipped=a['skipped'], skip_reason=a['skip_reason'], **fk)
        if not a['skipped']:
            entry = DeductionEntry.objects.create(
                category=PaidHolidayDeclaration.DEDUCTION_CODE,
                total_amount=a['amount'], currency=a['currency'], split_months=1,
                start_year=year, start_month=month,
                note=(f'Paid holiday {month:02d}/{year}: {a["days"]} day(s) '
                      f'x {a["daily_rate"]} (gross {a["gross_used"]} / '
                      f'{a["period_days"]} days)')[:500],
                **fk)
            award.deduction_entry = entry
            created += 1
        award.save()

    log_audit(actor, AuditLog.ACTION_CREATE, decl,
              note=(f'Paid holidays confirmed for {month:02d}/{year}: '
                    f'{len(payable)} day(s), {created} employee(s) credited')[:255])
    logger.info('Paid holidays confirmed %s/%s by %s — %s entries',
                month, year, actor, created)
    return decl, created


@transaction.atomic
def withdraw(decl, actor='', reason=''):
    """Undo a confirmation.

    Removes only the additions this declaration created, and only where the
    month has not been paid. An addition inside a settled month is money the
    employee has already received; deleting it would make the payslip and the
    record disagree.
    """
    from .models import PaidSalaryRecord

    paid_keys = set()
    for r in PaidSalaryRecord.objects.filter(year=decl.year, month=decl.month):
        paid_keys.add(('inhouse', r.employee_id) if r.employee_id
                      else ('remote', r.remote_employee_id))

    removed = kept = 0
    for award in decl.awards.select_related('deduction_entry'):
        if award.deduction_entry_id is None:
            continue
        person_key = (award.employee_type,
                      award.employee_id or award.remote_employee_id)
        if person_key in paid_keys:
            kept += 1
            continue
        entry_id = award.deduction_entry_id
        award.deduction_entry = None
        award.skipped = True
        award.skip_reason = 'withdrawn'
        award.save(update_fields=['deduction_entry', 'skipped', 'skip_reason'])
        DeductionEntry.objects.filter(id=entry_id).delete()
        removed += 1

    decl.status = PaidHolidayDeclaration.STATUS_WITHDRAWN
    decl.withdrawn_by = actor
    decl.withdrawn_at = timezone.now()
    decl.withdrawn_reason = (reason or '')[:255]
    decl.save(update_fields=['status', 'withdrawn_by', 'withdrawn_at', 'withdrawn_reason'])

    log_audit(actor, AuditLog.ACTION_UPDATE, decl,
              note=(f'Paid holidays withdrawn for {decl.month:02d}/{decl.year}: '
                    f'{removed} removed, {kept} left in place (already paid): {reason}')[:255])
    logger.info('Paid holidays withdrawn %s/%s by %s — %s removed, %s kept',
                decl.month, decl.year, actor, removed, kept)
    return removed, kept
