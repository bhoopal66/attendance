"""
One-time command to re-save all RemoteCallRecords so that
calculate_attendance_status() runs with the updated fixed-salary logic
(at least one call = present, zero calls = absent).

Usage:
    python manage.py fix_remote_attendance          # dry-run (show what would change)
    python manage.py fix_remote_attendance --apply   # actually save
"""

from django.core.management.base import BaseCommand

from attendance.models import RemoteCallRecord


class Command(BaseCommand):
    help = 'Re-save RemoteCallRecords to recalculate attendance_status for fixed-salary employees'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Actually save changes (default is dry-run)')

    def handle(self, *args, **options):
        apply = options['apply']
        records = RemoteCallRecord.objects.filter(employee__is_fixed_salary=True)
        total = records.count()
        changed = 0

        self.stdout.write(f'Found {total} call records for fixed-salary employees')

        for record in records.iterator():
            old_status = record.attendance_status
            new_status = record.calculate_attendance_status()
            if old_status != new_status:
                changed += 1
                self.stdout.write(
                    f'  {record.employee.name} {record.date}: {old_status} → {new_status}'
                )
                if apply:
                    record.attendance_status = new_status
                    record.save(update_fields=['attendance_status'])

        if apply:
            self.stdout.write(self.style.SUCCESS(f'Updated {changed}/{total} records'))
            self.stdout.write('Run recalculate_summaries for affected months to update monthly totals.')
        else:
            self.stdout.write(f'\n{changed}/{total} records would change. Run with --apply to save.')
