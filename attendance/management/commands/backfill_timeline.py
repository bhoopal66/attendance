"""Build each employee's timeline from what the database already knows.

    manage.py backfill_timeline            # report only
    manage.py backfill_timeline --apply

Reads, and writes one event each for:

    joining              from joining_date
    assignment changes   from EmployeeAssignment (promotion, transfer, ...)
    salary revisions     from SalaryStructure, with the amount
    approved leave       from LeaveRequest
    annual leave         from AnnualLeave
    leaving              from leaving_date

Every write goes through services_timeline.record(), which is keyed, so running
this twice does not double anybody's history. That is the whole reason the key
exists.

It does NOT invent an event for anything that has no date on record. A timeline
whose dates are guesses is worse than a short one.
"""
from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = "Populate EmployeeTimelineEvent from existing records. Idempotent."

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **opts):
        from attendance.models import (
            AnnualLeave, Employee, EmployeeAssignment, LeaveRequest,
            RemoteEmployee, SalaryStructure,
        )
        from attendance import services_timeline as tl

        w = self.stdout.write
        apply_it = opts['apply']
        planned = []

        def plan(person, date, category, etype, title, detail='', sm='', sid=''):
            if not date:
                return
            planned.append((person, date, category, etype, title, detail, sm, sid))

        for model in (Employee, RemoteEmployee):
            for emp in model.objects.all().order_by('name'):
                plan(emp, emp.joining_date, 'employment', 'joined',
                     'Joined', f'Department: {emp.department or "—"}')
                plan(emp, emp.leaving_date, 'employment', 'left', 'Left the company')

        for a in EmployeeAssignment.objects.select_related('employee', 'remote_employee'):
            person = a.employee or a.remote_employee
            if person is None or a.change_type == EmployeeAssignment.CHANGE_JOINING:
                continue        # joining is already covered above
            bits = [b for b in (a.designation, a.department, a.grade) if b]
            plan(person, a.effective_from,
                 'promotion' if a.change_type == EmployeeAssignment.CHANGE_PROMOTION else 'employment',
                 a.change_type, a.get_change_type_display(), ' · '.join(bits),
                 'EmployeeAssignment', a.pk)

        for s in SalaryStructure.objects.select_related('employee'):
            total = sum(float(getattr(s, f) or 0) for f in
                        ('basic', 'housing', 'transport', 'phone', 'other_allowance'))
            plan(s.employee, s.effective_from, 'salary', 'salary_revision',
                 f'Salary {total:,.2f} {s.currency or ""}'.strip(),
                 s.revision_reason or '', 'SalaryStructure', s.pk)

        for lr in LeaveRequest.objects.filter(status='approved').select_related('employee'):
            plan(lr.employee, lr.start_date, 'leave', 'leave_taken',
                 f'{lr.get_leave_type_display()} — {lr.start_date} to {lr.end_date}',
                 lr.reason or '', 'LeaveRequest', lr.pk)

        for al in AnnualLeave.objects.select_related('employee', 'remote_employee'):
            person = al.employee or al.remote_employee
            plan(person, al.start_date, 'leave', 'annual_leave',
                 f'Annual leave — {al.start_date} to {al.end_date}',
                 f'{al.salary_percentage}% paid' if al.is_paid else 'unpaid',
                 'AnnualLeave', al.pk)

        by_type = {}
        for _, _, _, etype, _, _, _, _ in planned:
            by_type[etype] = by_type.get(etype, 0) + 1

        w('')
        w('EVENTS TO WRITE')
        w('-' * 44)
        for k, v in sorted(by_type.items()):
            w(f'  {k:<24}{v:>5}')
        w(f'  {"TOTAL":<24}{len(planned):>5}')
        w('')
        w('  (already-present events are skipped by the dedupe key,')
        w('   so the number actually written may be lower)')

        if not apply_it:
            w('')
            w(self.style.WARNING('DRY RUN — nothing was written.'))
            return

        written = 0
        with transaction.atomic():
            for person, date, category, etype, title, detail, sm, sid in planned:
                before = tl.timeline(person).count()
                tl.record(person, date, category, etype, title, detail,
                          source_model=sm, source_id=sid, actor='backfill')
                if tl.timeline(person).count() > before:
                    written += 1
        w('')
        w(self.style.SUCCESS(f'WROTE {written} new event(s); {len(planned) - written} already existed.'))
