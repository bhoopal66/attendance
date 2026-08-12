"""
One-time migration: convert FrozenPayrollMonth snapshots into per-employee
PaidSalaryRecord entries so the new test dashboard can lock historical values
through its existing PaidSalaryRecord overlay mechanism.

Run once after deploying the new main dashboard:
    python manage.py convert_frozen_to_paid
    python manage.py convert_frozen_to_paid --dry-run   # preview only
"""

from decimal import Decimal

from django.core.management.base import BaseCommand

from attendance.models import Employee, RemoteEmployee
from payroll.models import Bank, FrozenPayrollMonth, PaidSalaryRecord


class Command(BaseCommand):
    help = 'Convert FrozenPayrollMonth records into PaidSalaryRecord entries'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Preview without writing')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        banks = list(Bank.objects.all().order_by('name'))

        frozen_qs = FrozenPayrollMonth.objects.all().order_by('year', 'month')
        if not frozen_qs.exists():
            self.stdout.write('No FrozenPayrollMonth records found.')
            return

        total_created = total_skipped = 0

        for frozen in frozen_qs:
            snap = frozen.snapshot
            year, month = frozen.year, frozen.month
            self.stdout.write(f'\nProcessing {year}/{month} (frozen by {frozen.frozen_by})')

            # Build lookup: (employee_id, employee_type) -> section row
            # admin_data = in-house Admin rows; all_sales_data = in-house Sales + remote rows
            section_by_emp = {}
            for row in snap.get('admin_data', []):
                key = (row['employee_id'], row.get('employee_type', 'inhouse'))
                section_by_emp[key] = row
            for row in snap.get('all_sales_data', []):
                key = (row['employee_id'], row.get('employee_type', 'inhouse'))
                section_by_emp[key] = row

            created = skipped = 0

            for fr in snap.get('final_rows', []):
                emp_id = fr['employee_id']
                emp_type = fr.get('employee_type', 'inhouse')
                sec = section_by_emp.get((emp_id, emp_type), {})

                # Reconstruct bank submissions list from stored bank_counts_list
                bank_submissions = []
                bank_counts = sec.get('bank_counts_list') or []
                for i, b in enumerate(banks):
                    bank_submissions.append({
                        'bank_id': b.id,
                        'bank_name': b.name,
                        'count': bank_counts[i] if i < len(bank_counts) else 0,
                        'rate_aed': float(b.per_account_charge),
                        'rate_inr': float(b.inr_per_account_charge) if b.inr_per_account_charge else None,
                    })

                # Build PaidSalaryRecord snapshot compatible with the test dashboard overlay.
                # Field mapping: old final_row uses 'payroll_net'; new snapshot uses 'net_payroll'.
                # carryover_in / carryover_out were not tracked in old final_rows — default to 0.
                snapshot = {
                    'employee_name': fr.get('employee_name', ''),
                    'employee_type': emp_type,
                    'department': fr.get('department', ''),
                    'currency': fr.get('currency', 'AED'),
                    'designation': fr.get('employee_designation', ''),
                    'salary': sec.get('salary', 0.0),
                    'payroll_type': 'attendance',
                    'is_fixed_salary': fr.get('employee_is_fixed_salary', False),
                    # Legacy marker — allows templates to distinguish converted records
                    'legacy_from_frozen': True,
                    'frozen_at': frozen.frozen_at.isoformat(),
                    # Attendance (from section row when available)
                    'full_days': sec.get('full_days'),
                    'half_days': sec.get('half_days'),
                    'absent_days': sec.get('absent_days'),
                    'late_days': sec.get('late_days'),
                    'late_half_days': sec.get('late_half_days', 0),
                    'present_days': sec.get('present_days'),
                    # Payroll line items
                    'net_payroll': fr['payroll_net'],  # renamed from old 'payroll_net'
                    'deduction': sec.get('deduction', 0),
                    'commission': sec.get('commission', 0),
                    'incentives': sec.get('incentives', 0),
                    'reductions': sec.get('reductions', 0),
                    'bank_submissions': bank_submissions,
                    # Deductions & additions
                    'deductions_breakdown': {},
                    'carryover_in': 0.0,
                    'total_deductions': fr.get('total_deductions', 0),
                    'total_additions': fr.get('total_additions', 0),
                    # Final
                    'carryover_out': 0.0,
                    'final_salary': fr['final_salary'],
                }

                final_salary_dec = Decimal(str(fr['final_salary']))
                currency = fr.get('currency', 'AED')

                try:
                    if emp_type == 'inhouse':
                        emp_obj = Employee.objects.get(pk=emp_id)
                        if not dry_run:
                            PaidSalaryRecord.objects.update_or_create(
                                employee=emp_obj, remote_employee=None, year=year, month=month,
                                defaults={
                                    'final_salary': final_salary_dec,
                                    'currency': currency,
                                    'paid_at': frozen.frozen_at,
                                    'paid_by': frozen.frozen_by,
                                    'snapshot': snapshot,
                                },
                            )
                    else:
                        emp_obj = RemoteEmployee.objects.get(pk=emp_id)
                        if not dry_run:
                            PaidSalaryRecord.objects.update_or_create(
                                remote_employee=emp_obj, employee=None, year=year, month=month,
                                defaults={
                                    'final_salary': final_salary_dec,
                                    'currency': currency,
                                    'paid_at': frozen.frozen_at,
                                    'paid_by': frozen.frozen_by,
                                    'snapshot': snapshot,
                                },
                            )
                    verb = 'Would create' if dry_run else 'Created'
                    self.stdout.write(f'  {verb}: {emp_type} #{emp_id} ({fr.get("employee_name")}) → final_salary={fr["final_salary"]}')
                    created += 1
                except Employee.DoesNotExist:
                    self.stderr.write(f'  Skip: inhouse #{emp_id} not found in DB')
                    skipped += 1
                except RemoteEmployee.DoesNotExist:
                    self.stderr.write(f'  Skip: remote #{emp_id} not found in DB')
                    skipped += 1

            self.stdout.write(f'  {year}/{month}: {created} records {"previewed" if dry_run else "created/updated"}, {skipped} skipped')
            total_created += created
            total_skipped += skipped

        self.stdout.write(self.style.SUCCESS(
            f'\nDone. Total: {total_created} records {"previewed" if dry_run else "created/updated"}, {total_skipped} skipped'
        ))
