"""Seed visa history from the visa documents already on file.

    manage.py backfill_visas            # report only
    manage.py backfill_visas --apply

Without this the visa table is empty, and "visa history" shows nothing for
everybody — which reads as "this person has never held a visa" rather than "we
started recording last week".

WHAT IT TAKES, AND WHAT IT REFUSES TO GUESS
-------------------------------------------
From the most recent `uae_visa` EmployeeDocument: number, issue date, expiry
date, and the document itself as the linked scan. From the employee row:
`visa_type`, and `sponsor_type='company'` where a visa provider is recorded —
a provider-sponsored permit IS company-sponsored.

Left blank: UID, visa file number, place of issue, inside/outside country.
None of them exist anywhere in the data today, and a blank field asks a
question while a fabricated one ends a conversation.

Employees with no visa document are SKIPPED and named. Anyone who already has a
visa row is left alone.
"""
from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = 'Create an opening EmployeeVisa row from each employee\'s visa document.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **opts):
        from attendance.models import (
            Employee, EmployeeDocument, EmployeeVisa, RemoteEmployee,
        )

        w = self.stdout.write
        apply_it = opts['apply']
        plan, has_visa, no_doc = [], [], []

        for model, kind in ((Employee, 'in-house'), (RemoteEmployee, 'remote')):
            for emp in model.objects.all().order_by('name'):
                key = ({'employee': emp, 'remote_employee': None} if model is Employee
                       else {'employee': None, 'remote_employee': emp})
                if EmployeeVisa.objects.filter(**key).exists():
                    has_visa.append((kind, emp))
                    continue
                doc = (EmployeeDocument.objects.filter(document_type='uae_visa', **key)
                       .order_by('-expiry_date', '-id').first())
                if doc is None:
                    no_doc.append((kind, emp))
                    continue
                plan.append((kind, emp, key, doc))

        w('')
        w(f'{"TYPE":<10}{"NAME":<26}{"PERMIT NO":<22}{"ISSUED":<12}{"EXPIRES":<12}TYPE')
        w('-' * 100)
        for kind, emp, _, doc in plan:
            w(f'{kind:<10}{(emp.name or "")[:25]:<26}{(doc.document_number or "—")[:21]:<22}'
              f'{str(doc.issue_date or "—"):<12}{str(doc.expiry_date or "—"):<12}'
              f'{getattr(emp, "visa_type", "") or "—"}')

        w('')
        w('SUMMARY')
        w('-' * 46)
        w(f'  {"visa rows to create":<30}{len(plan):>4}')
        w(f'  {"already have visa history":<30}{len(has_visa):>4}')
        w(f'  {"no visa document — SKIPPED":<30}{len(no_doc):>4}')

        if no_doc:
            w('')
            w(self.style.WARNING(
                'No visa document on file for these, so there is nothing to build a '
                'visa record from. Skipped, not invented:'))
            for kind, emp in no_doc:
                w(f'    {kind:<10}{(emp.name or "")[:30]:<32}{emp.tcr_id or "-"}')

        if not apply_it:
            w('')
            w(self.style.WARNING(f'DRY RUN — nothing was written.'))
            return

        with transaction.atomic():
            for kind, emp, key, doc in plan:
                EmployeeVisa.objects.create(
                    residence_permit_number=doc.document_number or '',
                    visa_type=getattr(emp, 'visa_type', '') or '',
                    sponsor=getattr(emp, 'visa_provider', '') or '',
                    sponsor_type='company' if getattr(emp, 'visa_provider', '') else '',
                    place_of_issue=doc.issuing_country or '',
                    issue_date=doc.issue_date,
                    expiry_date=doc.expiry_date,
                    status='active', is_current=True,
                    document=doc, created_by='backfill', **key)
        w(self.style.SUCCESS(f'WROTE {len(plan)} visa record(s).'))
        w('')
        w('UID, file number and inside/outside country are blank on every one —')
        w('that data does not exist yet and was not invented. Fill them in as')
        w('visas are renewed.')
