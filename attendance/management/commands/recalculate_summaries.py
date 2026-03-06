"""
Management command to recalculate monthly summaries for all employees.

Usage:
    python manage.py recalculate_summaries 2026 3
    python manage.py recalculate_summaries 2026 3 --employee-id 42
"""

import datetime
import calendar
import logging

from django.core.management.base import BaseCommand

from attendance.models import (
    AttendanceRecord, Employee, Holiday, LeaveRequest, MonthlySummary,
    RemoteCallRecord, RemoteEmployee, RemoteMonthlySummary,
)
from attendance.views.utils import (
    SATURDAY_WORK_DURATION_SECONDS,
    get_approved_leave_days, get_employee_shift_for_date, get_saturday_shift,
)

logger = logging.getLogger('attendance')


class Command(BaseCommand):
    help = 'Recalculate monthly summaries for employees'

    def add_arguments(self, parser):
        parser.add_argument('year', type=int, help='Year to recalculate')
        parser.add_argument('month', type=int, help='Month to recalculate (1-12)')
        parser.add_argument(
            '--employee-id', type=int,
            help='Recalculate for a specific employee ID only'
        )
        parser.add_argument(
            '--remote', action='store_true',
            help='Recalculate remote employee summaries instead'
        )

    def handle(self, *args, **options):
        year = options['year']
        month = options['month']
        employee_id = options.get('employee_id')
        is_remote = options.get('remote', False)

        if not (1 <= month <= 12):
            self.stderr.write(self.style.ERROR('Month must be between 1 and 12'))
            return

        _, days_in_month = calendar.monthrange(year, month)
        month_start = datetime.date(year, month, 1)
        month_end = datetime.date(year, month, days_in_month)

        holiday_dates = set(
            Holiday.objects.filter(
                date__gte=month_start, date__lte=month_end
            ).values_list('date', flat=True)
        )

        if is_remote:
            self._recalculate_remote(year, month, days_in_month, holiday_dates, employee_id)
        else:
            self._recalculate_inhouse(
                year, month, days_in_month, month_start, month_end,
                holiday_dates, employee_id
            )

    def _recalculate_inhouse(self, year, month, days_in_month, month_start, month_end,
                             holiday_dates, employee_id):
        employees = Employee.objects.filter(is_active=True)
        if employee_id:
            employees = employees.filter(id=employee_id)

        sat_start, sat_end = get_saturday_shift()

        shift_duration_sat = SATURDAY_WORK_DURATION_SECONDS
        updated = 0

        total_workdays = sum(
            1 for day in range(1, days_in_month + 1)
            if datetime.date(year, month, day).weekday() != 6
            and datetime.date(year, month, day) not in holiday_dates
        )

        for emp in employees:
            shift_start, shift_end = get_employee_shift_for_date(emp, month_start)
            shift_duration_weekday = (
                (shift_end.hour * 60 + shift_end.minute) -
                (shift_start.hour * 60 + shift_start.minute)
            ) * 60

            records = AttendanceRecord.objects.filter(
                employee=emp, date__year=year, date__month=month
            )
            approved_leave_days = get_approved_leave_days(emp, month_start, month_end)

            working_days = 0
            late_days = 0
            half_days = 0
            paid_leave_count = 0

            for record in records:
                weekday = record.date.weekday()
                is_sunday = weekday == 6
                is_saturday = weekday == 5
                is_holiday = record.date in holiday_dates
                is_paid_leave = record.date.day in approved_leave_days

                if is_paid_leave:
                    if not is_sunday and not is_holiday:
                        paid_leave_count += 1
                    continue

                if is_sunday or is_holiday:
                    continue

                total_secs = record.work_duration.total_seconds() if record.work_duration else 0
                if total_secs == 0:
                    continue

                working_days += 1

                arrived_after_noon = record.first_in and record.first_in.hour >= 12 and not is_saturday

                if is_saturday:
                    time_in_ok = record.first_in and (
                        record.first_in.hour < sat_start.hour or
                        (record.first_in.hour == sat_start.hour and
                         record.first_in.minute <= sat_start.minute)
                    )
                    time_out_ok = record.last_out and (
                        record.last_out.hour > sat_end.hour or
                        (record.last_out.hour == sat_end.hour and
                         record.last_out.minute >= sat_end.minute)
                    )
                else:
                    time_in_ok = record.first_in and (
                        record.first_in.hour < shift_start.hour or
                        (record.first_in.hour == shift_start.hour and
                         record.first_in.minute <= shift_start.minute)
                    )
                    time_out_ok = record.last_out and (
                        record.last_out.hour > shift_end.hour or
                        (record.last_out.hour == shift_end.hour and
                         record.last_out.minute >= shift_end.minute)
                    )

                if record.first_in and not time_in_ok and not arrived_after_noon:
                    late_days += 1

                if arrived_after_noon or not time_out_ok:
                    half_days += 1

            # Count paid leave days not already counted from records
            for day_num in approved_leave_days:
                date = datetime.date(year, month, day_num)
                if date.weekday() != 6 and date not in holiday_dates:
                    # Only count if not already counted from a record above
                    if not records.filter(date=date).exists():
                        paid_leave_count += 1

            leave_days = max(0, total_workdays - working_days - paid_leave_count)

            MonthlySummary.objects.update_or_create(
                employee=emp, year=year, month=month,
                defaults={
                    'working_days': working_days,
                    'leave_days': leave_days,
                    'late_days': late_days,
                    'half_days': half_days,
                }
            )
            updated += 1

        self.stdout.write(self.style.SUCCESS(
            f'Recalculated summaries for {updated} in-house employees ({year}/{month})'
        ))

    def _recalculate_remote(self, year, month, days_in_month, holiday_dates, employee_id):
        employees = RemoteEmployee.objects.filter(is_active=True)
        if employee_id:
            employees = employees.filter(id=employee_id)

        updated = 0

        for emp in employees:
            records = RemoteCallRecord.objects.filter(
                employee=emp, date__year=year, date__month=month
            )

            present = half = absent = 0
            total_talk_seconds = 0

            for record in records:
                if record.date.weekday() == 6:
                    continue
                if record.attendance_status == 'present':
                    present += 1
                elif record.attendance_status == 'half_day':
                    half += 1
                else:
                    absent += 1

                if record.total_talk_duration:
                    total_talk_seconds += record.total_talk_duration.total_seconds()

            RemoteMonthlySummary.objects.update_or_create(
                employee=emp, year=year, month=month,
                defaults={
                    'present_days': present,
                    'half_days': half,
                    'absent_days': absent,
                    'total_talk_time': datetime.timedelta(seconds=total_talk_seconds),
                }
            )
            updated += 1

        self.stdout.write(self.style.SUCCESS(
            f'Recalculated summaries for {updated} remote employees ({year}/{month})'
        ))
