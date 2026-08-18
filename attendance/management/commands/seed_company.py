"""Create the first legal entity and put every existing employee in it.

    manage.py seed_company                          # report only, writes nothing
    manage.py seed_company --apply                  # create + assign
    manage.py seed_company --name "Taamul" --code TAM --apply

WHY A DEFAULT ENTITY AT ALL
---------------------------
`BaseEmployee.company` arrives nullable so the migration cannot fail on a live
database. But a nullable tenant key is a liability: every query that forgets to
filter on it silently works, and every report quietly includes rows that belong
to nobody. The column is only useful once it is required, and it can only be
made required once every row has a value.

So this fills the gap, once, visibly — then the follow-up migration makes it
NOT NULL. Entities after the first are added through the UI, as requested; this
command exists to bootstrap, not to manage.

It never reassigns anyone who already has a company. Moving an employee between
entities is a transfer, with a date and a reason, and it does not belong in a
bootstrap script.
"""
from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = 'Create the first Company and assign every unassigned employee to it.'

    def add_arguments(self, parser):
        parser.add_argument('--name', default='Taamul',
                            help='Trading name of the default entity (default: Taamul).')
        parser.add_argument('--code', default='TAM',
                            help='Short code (default: TAM).')
        parser.add_argument('--apply', action='store_true',
                            help='Actually create and assign. Without it, nothing is written.')

    def handle(self, *args, **opts):
        from attendance.models import Company, Employee, RemoteEmployee

        w = self.stdout.write
        name, code, apply_it = opts['name'], opts['code'], opts['apply']

        existing = list(Company.objects.all())
        w('')
        w('EXISTING ENTITIES')
        w('-' * 60)
        if existing:
            for c in existing:
                w(f'  {c.code:<10}{c.name:<32}{"active" if c.is_active else "inactive"}')
        else:
            w('  none — this database has no legal entity recorded yet.')

        target = Company.objects.filter(code=code).first()

        unassigned = {
            'in-house': list(Employee.objects.filter(company__isnull=True)),
            'remote': list(RemoteEmployee.objects.filter(company__isnull=True)),
        }
        assigned_counts = {}
        for model, kind in ((Employee, 'in-house'), (RemoteEmployee, 'remote')):
            for emp in model.objects.filter(company__isnull=False).select_related('company'):
                key = (kind, emp.company.code)
                assigned_counts[key] = assigned_counts.get(key, 0) + 1

        w('')
        w('EMPLOYEES')
        w('-' * 60)
        for (kind, ccode), n in sorted(assigned_counts.items()):
            w(f'  {kind:<10}already in {ccode:<10}{n:>4}')
        for kind, rows in unassigned.items():
            w(f'  {kind:<10}{"unassigned":<21}{len(rows):>4}')
        total_unassigned = sum(len(v) for v in unassigned.values())

        w('')
        w('PLAN')
        w('-' * 60)
        if target:
            w(f'  entity {code} already exists — it will be reused, not recreated')
        else:
            w(f'  create entity  {code} / {name}')
        if total_unassigned:
            w(f'  assign         {total_unassigned} employee(s) to {code}')
        else:
            w('  assign         nothing — every employee already has an entity')
        w('  touch          nobody who already has one')

        if not apply_it:
            w('')
            w(self.style.WARNING(
                'DRY RUN — nothing was written. Re-run with --apply when the plan reads right.'))
            w('Then add any further entities through the UI, and move people between')
            w('them one at a time — that is a transfer, not a bulk update.')
            return

        with transaction.atomic():
            if not target:
                target = Company.objects.create(code=code, name=name, is_active=True)
                w('')
                w(self.style.SUCCESS(f'CREATED entity {target}'))
            n = 0
            for model in (Employee, RemoteEmployee):
                n += model.objects.filter(company__isnull=True).update(company=target)
        w(self.style.SUCCESS(f'ASSIGNED {n} employee(s) to {target.code}.'))
        w('')
        w('Next: once you are satisfied every employee is in the right entity,')
        w('the follow-up migration makes the column required so a future employee')
        w('cannot be created without one.')
