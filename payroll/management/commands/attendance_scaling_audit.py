"""
Who is exempt from attendance scaling, and who honours approved leave.

    DJANGO_SETTINGS_MODULE=attendance_project.settings.production \
        python3 manage.py attendance_scaling_audit

Read only. Answers two questions that the payroll screen cannot:

  1. Whose salary does NOT move with attendance, and why.
  2. Who could hold an approved leave request at all.

The second matters because `LeaveRequest.employee` is a FK to `Employee` only.
A remote employee cannot have one, so "no approved leave" for a remote person
means "the model cannot record it", not "they took none". Reporting those two
states as the same thing is how a gap stays invisible.
"""

import csv

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'List employees exempt from attendance scaling and those who cannot hold leave requests.'

    def add_arguments(self, parser):
        parser.add_argument('--csv', dest='csv_path', default='')
        parser.add_argument('--include-inactive', action='store_true')

    def handle(self, *args, **opts):
        from attendance.models import Employee, RemoteEmployee

        rows = []
        for model, kind in ((Employee, 'inhouse'), (RemoteEmployee, 'remote')):
            qs = model.objects.all() if opts['include_inactive'] \
                else model.objects.filter(is_active=True)
            for emp in qs.order_by('name'):
                ptype = getattr(emp, 'payroll_type', 'attendance') or 'attendance'
                fixed = bool(getattr(emp, 'is_fixed_salary', False))
                salary = float(emp.salary or 0)

                if ptype == 'performance':
                    scaled, why = False, 'performance payroll — by policy'
                elif not salary:
                    scaled, why = False, 'NO SALARY ON RECORD — falls through silently'
                elif fixed:
                    scaled, why = True, 'fixed salary — punch-in counts as a full day'
                else:
                    scaled, why = True, 'attendance-scaled'

                rows.append({
                    'name': emp.name, 'tcr': emp.tcr_id or '', 'kind': kind,
                    'dept': emp.department or '', 'ptype': ptype,
                    'salary': salary, 'scaled': scaled, 'why': why,
                    'can_hold_leave': kind == 'inhouse',
                })

        hdr = (f'{"Employee":<24}{"Type":<9}{"Dept":<10}{"Payroll":<13}'
               f'{"Salary":>10}  {"Scaled":<7}{"Leave?":<8}Why')
        self.stdout.write(self.style.MIGRATE_HEADING('Attendance scaling audit'))
        self.stdout.write(hdr)
        self.stdout.write('-' * len(hdr))

        exempt = no_salary = cannot_hold = 0
        for r in rows:
            if not r['scaled']:
                exempt += 1
            if 'NO SALARY' in r['why']:
                no_salary += 1
            if not r['can_hold_leave']:
                cannot_hold += 1
            self.stdout.write(
                f'{r["name"][:23]:<24}{r["kind"]:<9}{r["dept"][:9]:<10}'
                f'{r["ptype"]:<13}{r["salary"]:>10,.0f}  '
                f'{("yes" if r["scaled"] else "NO"):<7}'
                f'{("yes" if r["can_hold_leave"] else "no"):<8}{r["why"]}')

        self.stdout.write('')
        self.stdout.write(f'  {len(rows)} employees')
        if exempt:
            self.stdout.write(self.style.WARNING(
                f'  {exempt} are NOT attendance-scaled — a full salary on their row is '
                f'not an attendance figure.'))
        if no_salary:
            self.stdout.write(self.style.ERROR(
                f'  {no_salary} have NO salary recorded and fall through the scaling gate '
                f'silently. This is the one entry above that is probably a data error '
                f'rather than a policy.'))
        if cannot_hold:
            self.stdout.write(self.style.WARNING(
                f'  {cannot_hold} are remote and CANNOT hold an approved leave request at '
                f'all — LeaveRequest has no remote FK. Their paid leave can only be '
                f'recorded as an AnnualLeave span.'))
        if not rows:
            self.stdout.write(self.style.WARNING('  Nothing matched — nothing was checked.'))

        if opts['csv_path']:
            with open(opts['csv_path'], 'w', newline='', encoding='utf-8') as fh:
                w = csv.writer(fh)
                w.writerow(['Name', 'TCR', 'Type', 'Department', 'Payroll type',
                            'Salary', 'Attendance scaled', 'Can hold leave request', 'Why'])
                for r in rows:
                    w.writerow([r['name'], r['tcr'], r['kind'], r['dept'], r['ptype'],
                                r['salary'], 'yes' if r['scaled'] else 'no',
                                'yes' if r['can_hold_leave'] else 'no', r['why']])
            self.stdout.write(f'\n  Written to {opts["csv_path"]}')
