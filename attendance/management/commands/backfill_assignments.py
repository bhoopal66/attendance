"""Give every employee the assignment they are on today, as row one of history.

    manage.py backfill_assignments            # report only
    manage.py backfill_assignments --apply

The table is worthless empty: "show me this person's history" would return
nothing for all 44 people, which reads as "no history" rather than "not
captured yet". This writes the opening row.

WHAT IT COPIES, AND WHAT IT REFUSES TO INVENT
---------------------------------------------
Copied verbatim from the employee row: department, team, location,
designation, reporting manager, company.

Left empty on purpose: grade, job level, cost centre (no such field exists
today), and `reason` (nobody recorded one, and writing "initial load" into a
reason column makes a guess look like a record).

`effective_from` is the joining date. Where that is missing the employee is
SKIPPED and named — an assignment has to start somewhere, and picking today
would claim the arrangement began the day the script ran.

Never touches anyone who already has an assignment.
"""
from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = 'Create the opening EmployeeAssignment row for every employee.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Actually write. Without it, nothing is saved.')

    def handle(self, *args, **opts):
        from attendance.models import Employee, EmployeeAssignment, RemoteEmployee

        w = self.stdout.write
        apply_it = opts['apply']

        to_create, skipped_existing, no_date = [], [], []

        for model, kind in ((Employee, 'in-house'), (RemoteEmployee, 'remote')):
            for emp in model.objects.all().order_by('name'):
                key = ({'employee': emp, 'remote_employee': None} if model is Employee
                       else {'employee': None, 'remote_employee': emp})
                if EmployeeAssignment.objects.filter(**key).exists():
                    skipped_existing.append((kind, emp))
                    continue
                if not emp.joining_date:
                    no_date.append((kind, emp))
                    continue
                to_create.append((kind, emp, key))

        w('')
        w(f'{"TYPE":<10}{"NAME":<26}{"FROM":<12}{"DEPARTMENT":<18}{"DESIGNATION":<22}MANAGER')
        w('-' * 120)
        for kind, emp, _ in to_create:
            w(f'{kind:<10}{(emp.name or "")[:25]:<26}{str(emp.joining_date):<12}'
              f'{(emp.department or "—")[:17]:<18}'
              f'{(getattr(emp, "designation", "") or "—")[:21]:<22}'
              f'{getattr(getattr(emp, "reporting_manager", None), "name", "—")}')

        w('')
        w('SUMMARY')
        w('-' * 46)
        w(f'  {"opening rows to write":<28}{len(to_create):>4}')
        w(f'  {"already have history":<28}{len(skipped_existing):>4}')
        w(f'  {"no joining date — SKIPPED":<28}{len(no_date):>4}')

        if no_date:
            w('')
            w(self.style.WARNING(
                'These have no joining date, so there is no honest date to start '
                'their history from. They are skipped, not guessed at:'))
            for kind, emp in no_date:
                w(f'    {kind:<10}{(emp.name or "")[:30]:<32}{emp.tcr_id or "-"}')
            w('  Set a joining date and re-run.')

        if not apply_it:
            w('')
            w(self.style.WARNING(
                f'DRY RUN — nothing was written. {len(to_create)} row(s) would be created.'))
            return

        with transaction.atomic():
            for kind, emp, key in to_create:
                EmployeeAssignment.objects.create(
                    effective_from=emp.joining_date,
                    effective_to=None,
                    is_current=True,
                    change_type=EmployeeAssignment.CHANGE_JOINING,
                    company=getattr(emp, 'company', None),
                    department=emp.department or '',
                    team=emp.team or '',
                    location=emp.location or '',
                    designation=getattr(emp, 'designation', '') or '',
                    reporting_manager=getattr(emp, 'reporting_manager', None),
                    created_by='backfill',
                    **key)
        w(self.style.SUCCESS(f'WROTE {len(to_create)} opening assignment(s).'))
        if no_date:
            w(self.style.WARNING(f'{len(no_date)} still have no history — they need a joining date.'))
