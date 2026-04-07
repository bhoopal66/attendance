"""
Management command to detect and merge duplicate in-house Employee records.

Duplicates arise when the biometric machine switches between padded IDs
(e.g. 00000015) and short IDs (e.g. 15) for the same person. Because the
unique constraint is (person_id, name), each format creates a separate row,
splitting attendance and payroll data across two records.

Strategy for each duplicate pair:
  - KEEPER: the record with more attendance data (or the one with a department
            set, or the lower Django id as a final tiebreak).
  - DUPE:   the other record — its data is re-pointed to the keeper, its
            person_id is archived as an alias, then it is deactivated.

Usage:
  python manage.py merge_duplicate_employees           # dry-run (safe, no changes)
  python manage.py merge_duplicate_employees --apply   # actually apply changes
"""

import logging
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count

from attendance.models import (
    AttendanceRecord, Employee, EmployeeIDAlias,
    EarlyLeaveRequest, LeaveRequest, MonthlySummary, ShiftHistory,
)
from payroll.models import BankSubmission, PayrollAdjustment

logger = logging.getLogger('attendance')


def _pick_keeper(a: Employee, b: Employee) -> tuple[Employee, Employee]:
    """Return (keeper, dupe) based on data richness."""
    a_att = AttendanceRecord.objects.filter(employee=a).count()
    b_att = AttendanceRecord.objects.filter(employee=b).count()

    if a_att != b_att:
        return (a, b) if a_att > b_att else (b, a)

    # Equal attendance — prefer the one with a department set
    if a.department and not b.department:
        return a, b
    if b.department and not a.department:
        return b, a

    # Prefer the one with salary set
    if a.salary and not b.salary:
        return a, b
    if b.salary and not a.salary:
        return b, a

    # Default: keep lower Django id (older record)
    return (a, b) if a.id < b.id else (b, a)


class Command(BaseCommand):
    help = 'Merge duplicate Employee records caused by biometric machine ID format changes'

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            default=False,
            help='Actually apply the merge. Without this flag, the command runs in dry-run mode.',
        )

    def handle(self, *args, **options):
        apply = options['apply']
        mode = 'APPLY' if apply else 'DRY-RUN'
        self.stdout.write(self.style.WARNING(f'\n=== merge_duplicate_employees [{mode}] ===\n'))

        # Find all active employee names that appear more than once
        dup_names = (
            Employee.objects
            .filter(is_active=True)
            .values('name')
            .annotate(cnt=Count('id'))
            .filter(cnt__gt=1)
            .values_list('name', flat=True)
        )

        if not dup_names:
            self.stdout.write(self.style.SUCCESS('No duplicate active employees found. Nothing to do.'))
            return

        self.stdout.write(f'Found {len(dup_names)} name(s) with multiple active records:\n')

        total_merged = 0

        for name in sorted(dup_names):
            records = list(Employee.objects.filter(name=name, is_active=True).order_by('id'))
            self.stdout.write(f'  {name} ({len(records)} records)')
            for r in records:
                att = AttendanceRecord.objects.filter(employee=r).count()
                summ = MonthlySummary.objects.filter(employee=r).count()
                self.stdout.write(
                    f'    id={r.id:<4} person_id={r.person_id:<12} '
                    f'dept={r.department or "—":<8} '
                    f'salary={r.salary or "—":<8} '
                    f'att={att:<5} summaries={summ}'
                )

            # Merge all records into one keeper
            # Process pairs one at a time until only one active record remains
            while True:
                active = list(Employee.objects.filter(name=name, is_active=True).order_by('id'))
                if len(active) <= 1:
                    break

                keeper, dupe = _pick_keeper(active[0], active[1])
                self._merge_pair(keeper, dupe, apply)
                total_merged += 1

            keeper_final = Employee.objects.filter(name=name, is_active=True).first()
            self.stdout.write(
                self.style.SUCCESS(f'    → Canonical record: id={keeper_final.id} person_id={keeper_final.person_id}\n')
            )

        if apply:
            self.stdout.write(self.style.SUCCESS(
                f'\nDone. Merged {total_merged} duplicate record(s). '
                f'Run: python manage.py recalculate_summaries <year> <month> to rebuild monthly summaries.'
            ))
        else:
            self.stdout.write(self.style.WARNING(
                f'\nDry-run complete. {total_merged} merge(s) would be applied.\n'
                f'Run with --apply to execute.'
            ))

    def _merge_pair(self, keeper: Employee, dupe: Employee, apply: bool):
        """Re-point all of dupe's data to keeper, archive alias, deactivate dupe."""
        self.stdout.write(
            f'    MERGE: keep id={keeper.id} ({keeper.person_id}) '
            f'← absorb id={dupe.id} ({dupe.person_id})'
        )

        # Count what will move
        att_records     = AttendanceRecord.objects.filter(employee=dupe)
        summaries       = MonthlySummary.objects.filter(employee=dupe)
        leave_requests  = LeaveRequest.objects.filter(employee=dupe)
        early_requests  = EarlyLeaveRequest.objects.filter(employee=dupe)
        shift_history   = ShiftHistory.objects.filter(employee=dupe)
        adjustments     = PayrollAdjustment.objects.filter(employee=dupe)
        submissions     = BankSubmission.objects.filter(employee=dupe)

        # Check for date conflicts in AttendanceRecord
        dupe_dates  = set(att_records.values_list('date', flat=True))
        keeper_dates = set(AttendanceRecord.objects.filter(employee=keeper).values_list('date', flat=True))
        conflicts   = dupe_dates & keeper_dates

        if conflicts:
            self.stdout.write(
                self.style.WARNING(
                    f'      WARNING: {len(conflicts)} date conflict(s) in AttendanceRecord '
                    f'(keeper also has records on these dates). '
                    f'Conflicting records from dupe will be DELETED (keeper\'s records take priority).'
                )
            )

        # Check for month conflicts in MonthlySummary
        dupe_months   = set(summaries.values_list('year', 'month'))
        keeper_months = set(MonthlySummary.objects.filter(employee=keeper).values_list('year', 'month'))
        month_conflicts = dupe_months & keeper_months

        self.stdout.write(
            f'      Moving: {att_records.count()} attendance records '
            f'({len(conflicts)} conflict), '
            f'{summaries.count()} summaries ({len(month_conflicts)} conflict), '
            f'{leave_requests.count()} leave reqs, '
            f'{early_requests.count()} early-leave reqs, '
            f'{adjustments.count()} payroll adj, '
            f'{submissions.count()} bank submissions'
        )

        if not apply:
            return

        with transaction.atomic():
            # AttendanceRecord — delete conflicts then re-point the rest
            if conflicts:
                att_records.filter(date__in=conflicts).delete()
            AttendanceRecord.objects.filter(employee=dupe).update(employee=keeper)

            # MonthlySummary — delete conflicts then re-point
            if month_conflicts:
                for yr, mo in month_conflicts:
                    summaries.filter(year=yr, month=mo).delete()
            MonthlySummary.objects.filter(employee=dupe).update(employee=keeper)

            # Everything else — just re-point
            LeaveRequest.objects.filter(employee=dupe).update(employee=keeper)
            EarlyLeaveRequest.objects.filter(employee=dupe).update(employee=keeper)
            ShiftHistory.objects.filter(employee=dupe).update(employee=keeper)
            PayrollAdjustment.objects.filter(employee=dupe).update(employee=keeper)
            BankSubmission.objects.filter(employee=dupe).update(employee=keeper)

            # Archive the dupe's person_id as an alias on the keeper
            EmployeeIDAlias.objects.get_or_create(employee=keeper, person_id=dupe.person_id)

            # Copy over any missing profile data from dupe → keeper
            changed = False
            for field in ('department', 'salary', 'designation', 'tcr_id',
                          'email', 'phone', 'location', 'team', 'joining_date'):
                if not getattr(keeper, field) and getattr(dupe, field):
                    setattr(keeper, field, getattr(dupe, field))
                    changed = True
            if changed:
                keeper.save()

            # Deactivate the dupe
            dupe.is_active = False
            dupe.save(update_fields=['is_active', 'updated_at'])

            logger.info(
                'Merged duplicate employee: kept id=%s (%s), deactivated id=%s (%s)',
                keeper.id, keeper.person_id, dupe.id, dupe.person_id,
            )
