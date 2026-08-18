"""Derive each employee's labour jurisdiction from what is already on record.

REPORT ONLY unless --apply is passed. Nothing is written by default.

WHY A COMMAND AND NOT A DATA MIGRATION
--------------------------------------
A data migration would classify 44 people's employment regime silently, inside
a deploy, with no chance to look at the list first. Jurisdiction decides whether
UAE labour law applies to someone. That is not a field to guess at in the dark.

THE RULES, AND THEIR CONFIDENCE
-------------------------------
    paid in INR or NPR            -> offshore
        Not a UAE salary. These are staff working from India and Nepal, with no
        visa, no salary structure and no ability to hold a leave request. High
        confidence.

    paid in AED, visa provider set -> mohre
        The company sponsors their work permit through Jumbo, OnTime or Taamul.
        That IS a MOHRE work permit. High confidence.

    paid in AED, no visa provider  -> LEFT BLANK, listed for review
        Could be own/spouse visa, could be a MOHRE employee whose provider was
        never recorded. The two have different statutory consequences and the
        data cannot tell them apart. Guessing here would put a legal
        classification on someone based on a missing field, so it refuses and
        names them instead.

Already-set values are never touched unless --overwrite is given.
"""
from django.core.management.base import BaseCommand
from django.db import transaction


OFFSHORE_CURRENCIES = {'INR', 'NPR'}


def classify(emp):
    """(jurisdiction_or_None, reason)."""
    currency = (getattr(emp, 'currency', '') or '').upper()
    provider = (getattr(emp, 'visa_provider', '') or '').strip()

    if currency in OFFSHORE_CURRENCIES:
        return 'offshore', f'paid in {currency} — not a UAE salary'
    if currency == 'AED' and provider:
        return 'mohre', f'company-sponsored work permit via {provider}'
    if currency == 'AED':
        return None, 'AED but no visa provider on record — own/spouse visa or an unrecorded provider; cannot tell'
    return None, f'currency {currency or "not set"} — nothing to classify on'


class Command(BaseCommand):
    help = 'Derive labour_jurisdiction from currency and visa provider. Reports by default; --apply writes.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Actually write. Without it, nothing is saved.')
        parser.add_argument('--overwrite', action='store_true',
                            help='Also change employees who already have a jurisdiction set.')

    def handle(self, *args, **opts):
        from attendance.models import Employee, RemoteEmployee

        apply_it = opts['apply']
        overwrite = opts['overwrite']

        rows = []
        for model, kind in ((Employee, 'in-house'), (RemoteEmployee, 'remote')):
            for emp in model.objects.all().order_by('name'):
                current = getattr(emp, 'labour_jurisdiction', '') or ''
                verdict, reason = classify(emp)
                rows.append((kind, emp, current, verdict, reason))

        w = self.stdout.write
        w('')
        w(f'{"TYPE":<9}{"NAME":<26}{"TCR":<14}{"CUR":<5}{"NOW":<14}{"->":<3}{"PROPOSED":<14}REASON')
        w('-' * 150)

        counts = {}
        skipped_set = []
        review = []
        to_write = []

        for kind, emp, current, verdict, reason in rows:
            if current and not overwrite:
                skipped_set.append((emp, current))
                shown = '(keeps)'
            elif verdict is None:
                review.append((kind, emp, reason))
                shown = 'REVIEW'
            else:
                to_write.append((emp, verdict))
                shown = verdict
                counts[verdict] = counts.get(verdict, 0) + 1
            w(f'{kind:<9}{(emp.name or "")[:25]:<26}{(emp.tcr_id or "-"):<14}'
              f'{(emp.currency or "-"):<5}{(current or "—"):<14}{"->":<3}{shown:<14}{reason}')

        w('')
        w('SUMMARY')
        w('-' * 40)
        for k, v in sorted(counts.items()):
            w(f'  {k:<16}{v:>4}')
        w(f'  {"needs review":<16}{len(review):>4}')
        w(f'  {"already set":<16}{len(skipped_set):>4}'
          f'{"  (use --overwrite to change these)" if skipped_set and not overwrite else ""}')
        w(f'  {"TOTAL":<16}{len(rows):>4}')

        if review:
            w('')
            w(self.style.WARNING(
                'THESE ARE NOT CLASSIFIED. They are left blank on purpose — the data '
                'cannot distinguish own/spouse visa from a MOHRE employee whose '
                'provider was never recorded, and the two are not the same in law:'))
            for kind, emp, reason in review:
                w(f'    {kind:<9}{(emp.name or "")[:30]:<32}{emp.tcr_id or "-"}')
            w('  Set these by hand, or record their visa provider and re-run.')

        w('')
        if not apply_it:
            w(self.style.WARNING(
                f'DRY RUN — nothing was written. {len(to_write)} employee(s) would change. '
                'Re-run with --apply once the list above is right.'))
            return

        with transaction.atomic():
            for emp, verdict in to_write:
                emp.labour_jurisdiction = verdict
                emp.save(update_fields=['labour_jurisdiction'])
        w(self.style.SUCCESS(f'WROTE {len(to_write)} employee(s).'))
        if review:
            w(self.style.WARNING(f'{len(review)} left blank and still need a decision.'))
