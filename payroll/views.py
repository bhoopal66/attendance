"""
Payroll calculation views.
"""

import io
import json
import datetime
import calendar
import logging
from collections import defaultdict
from decimal import Decimal

from django.core.management import call_command
from django.db.models import Sum
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, get_object_or_404
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required, user_passes_test

from attendance.models import (
    Employee, RemoteEmployee, Holiday,
    LeaveRequest, MonthlySummary, RemoteMonthlySummary, AnnualLeave,
    AttendanceRecord, RemoteCallRecord,
)
from collections import defaultdict as _defaultdict
from attendance.views.utils import (
    MONTH_CHOICES, MONTH_NAMES, YEAR_RANGE,
    get_selected_month_year, superuser_required,
)
from .models import PayrollAdjustment, Bank, BankSubmission, DeductionEntry, DEDUCTION_CATEGORY_CHOICES

logger = logging.getLogger('payroll')


# ============================================
# Payroll Calculation Helpers
# ============================================

def _count_holidays(year, month, days_in_month):
    """Return total non-working days (Sundays + custom holidays) for a month.
    Uses a set to avoid double-counting Sundays that are also custom holidays.
    """
    non_working = set(
        day for day in range(1, days_in_month + 1)
        if datetime.date(year, month, day).weekday() == 6
    )
    for holiday in Holiday.objects.filter(date__year=year, date__month=month):
        non_working.add(holiday.date.day)
    return len(non_working)


def _leave_days_in_month(leave, month_start, month_end):
    """Return the effective paid leave days that fall within the given month.
    Pro-rates approved_days based on how much of the leave span falls in the month.
    """
    effective = leave.get_effective_days()
    if not effective:
        return 0
    # If entirely within the month, no pro-rating needed
    if leave.start_date >= month_start and leave.end_date <= month_end:
        return effective
    # Clip to month boundaries and pro-rate
    overlap_start = max(leave.start_date, month_start)
    overlap_end = min(leave.end_date, month_end)
    full_span = (leave.end_date - leave.start_date).days + 1
    overlap_span = (overlap_end - overlap_start).days + 1
    return round(effective * overlap_span / full_span)


def _annual_leave_day_counts(al, month_start, month_end):
    """Return (working_days, non_working_days) for the annual leave overlap with the month.

    working_days     — Mon-Sat excluding custom holidays (these are already deducted
                       via absent_days in the monthly summary).
    non_working_days — Sundays + custom holidays within the leave period.
                       For paid leave  : ignored (never deducted, never compensated).
                       For unpaid leave: must be additionally deducted because the
                       employee should receive no pay for any day in that period.
    """
    overlap_start = max(al.start_date, month_start)
    overlap_end = min(al.end_date, month_end)
    if overlap_end < overlap_start:
        return 0, 0

    holiday_dates = set(
        Holiday.objects.filter(
            date__gte=overlap_start, date__lte=overlap_end
        ).values_list('date', flat=True)
    )

    working_days = 0
    non_working_days = 0
    current = overlap_start
    while current <= overlap_end:
        if current.weekday() != 6 and current not in holiday_dates:
            working_days += 1
        else:
            non_working_days += 1
        current += datetime.timedelta(days=1)
    return working_days, non_working_days


INR_COMMISSION_THRESHOLD = 4   # first N accounts use the bank's INR rate
INR_OVERFLOW_RATE = Decimal('3000')  # INR per account beyond the threshold


def _calc_inr_tiered_commission(pairs):
    """Tiered INR commission.

    pairs: list of (count, bank_inr_rate) in processing order (alphabetical by bank).
    - Accounts 1-INR_COMMISSION_THRESHOLD: bank's INR per-account charge.
    - Each account beyond threshold: INR_OVERFLOW_RATE flat.

    Returns (total_commission_float, per_pair_commission_list).
    """
    total = Decimal('0')
    used = 0
    per_pair = []
    for count, rate in pairs:
        rate = Decimal(str(rate))
        if used >= INR_COMMISSION_THRESHOLD:
            commission = count * INR_OVERFLOW_RATE
        elif used + count <= INR_COMMISSION_THRESHOLD:
            commission = count * rate
            used += count
        else:
            within = INR_COMMISSION_THRESHOLD - used
            overflow = count - within
            commission = within * rate + overflow * INR_OVERFLOW_RATE
            used = INR_COMMISSION_THRESHOLD
        per_pair.append(float(commission))
        total += commission
    return float(total), per_pair


def _get_commission(year, month, employee=None, remote_employee=None, currency='AED'):
    """Calculate total commission from bank submissions for the period.
    INR employees use tiered pricing: first INR_COMMISSION_THRESHOLD accounts at the
    bank's INR rate, then INR_OVERFLOW_RATE per account."""
    if employee:
        submissions = list(BankSubmission.objects.filter(
            employee=employee, year=year, month=month
        ).select_related('bank').order_by('bank__name'))
    else:
        submissions = list(BankSubmission.objects.filter(
            remote_employee=remote_employee, year=year, month=month
        ).select_related('bank').order_by('bank__name'))

    if currency == 'INR':
        pairs = [(s.submission_count, s.bank.charge_for_currency('INR')) for s in submissions]
        total, _ = _calc_inr_tiered_commission(pairs)
        return total

    return float(sum(
        s.submission_count * s.bank.per_account_charge
        for s in submissions
    ))


def _get_inhouse_payroll_row(emp, year, month, month_start, month_end, total_holidays):
    """Build a payroll data row for one in-house employee."""
    summary = MonthlySummary.objects.filter(
        employee=emp, year=year, month=month
    ).first()

    if summary:
        half_days = summary.half_days or 0
        full_days = summary.working_days - half_days
        effective_work_days = full_days + (half_days * 0.5)
        absent_days = summary.leave_days or 0
        late_days = summary.late_days or 0
    else:
        full_days = 0
        half_days = 0
        effective_work_days = 0.0
        absent_days = 0
        late_days = 0

    salary = float(emp.salary) if emp.salary else 0.0
    basic_salary = round(salary * 0.40, 2)
    housing_allowance = round(salary * 0.40, 2)
    transport_allowance = round(salary * 0.20, 2)

    approved_leaves = LeaveRequest.objects.filter(
        employee=emp,
        status='approved',
        start_date__lte=month_end,
        end_date__gte=month_start,
    )
    paid_leave_days = sum(_leave_days_in_month(leave, month_start, month_end) for leave in approved_leaves)

    days_in_month = calendar.monthrange(year, month)[1]
    daily_rate = salary / days_in_month if salary > 0 else 0.0
    # Every 3 late days = 1 half-day deduction
    late_half_days = late_days // 3

    # Performance-based payroll: no late/leave deductions
    if getattr(emp, 'payroll_type', 'attendance') == 'performance':
        total_deduction_days = 0
        deduction = 0.0
    else:
        # Deduct absent days, half-day shortfalls, and late penalties from full salary
        total_deduction_days = absent_days + (half_days * 0.5) + (late_half_days * 0.5)
        deduction = daily_rate * total_deduction_days
    base_payroll = salary - deduction

    adjustments = PayrollAdjustment.objects.filter(employee=emp, year=year, month=month)
    incentives = float(
        adjustments.filter(adjustment_type='incentive').aggregate(total=Sum('amount'))['total'] or 0
    )
    reductions = float(
        adjustments.filter(adjustment_type='reduction').aggregate(total=Sum('amount'))['total'] or 0
    )
    commission = _get_commission(year, month, employee=emp, currency=emp.currency)

    # Annual leave adjustment:
    # - Paid leave at X%: compensate X% of daily_rate × working_leave_days
    #   (offsets the absent-day deduction already applied via base_payroll)
    # - Unpaid leave: no compensation for working days (deduction stands),
    #   but also deduct Sundays + holidays within the leave period because
    #   those days are normally paid — on unpaid leave they should not be.
    annual_leaves = AnnualLeave.objects.filter(
        employee=emp,
        start_date__lte=month_end,
        end_date__gte=month_start,
    )
    annual_leave_compensation = 0.0
    annual_leave_extra_deduction = 0.0
    annual_leave_days = 0
    for al in annual_leaves:
        working_days, non_working_days = _annual_leave_day_counts(al, month_start, month_end)
        annual_leave_days += working_days
        salary_pct = float(al.salary_percentage) if al.is_paid else 0.0
        # Working days: compensate salary_pct% of daily rate (offsets absent-day deduction)
        annual_leave_compensation += daily_rate * working_days * salary_pct / 100.0
        # Non-working days (Sundays/holidays): normally paid at 100%, during leave
        # only paid at salary_pct% — deduct the remaining (100 - salary_pct)%
        annual_leave_extra_deduction += daily_rate * non_working_days * (100.0 - salary_pct) / 100.0

    net_payroll = base_payroll + annual_leave_compensation - annual_leave_extra_deduction + incentives + commission - reductions

    return {
        'employee': emp,
        'employee_type': 'inhouse',
        'currency': emp.currency,
        'salary': salary,
        'basic_salary': basic_salary,
        'housing_allowance': housing_allowance,
        'transport_allowance': transport_allowance,
        'full_days': full_days,
        'half_days': half_days,
        'effective_work_days': round(effective_work_days, 1),
        'absent_days': absent_days,
        'late_days': late_days,
        'late_half_days': late_half_days,
        'paid_leave_days': paid_leave_days,
        'annual_leave_days': annual_leave_days,
        'annual_leave_compensation': round(annual_leave_compensation, 2),
        'annual_leave_extra_deduction': round(annual_leave_extra_deduction, 2),
        'total_deduction_days': round(total_deduction_days, 1),
        'daily_rate': round(daily_rate, 2),
        'deduction': round(deduction, 2),
        'base_payroll': round(base_payroll, 2),
        'incentives': round(incentives, 2),
        'reductions': round(reductions, 2),
        'commission': round(commission, 2),
        'net_payroll': round(net_payroll, 2),
    }


def _get_sales_payroll_row(emp, year, month, emp_type, banks, days_in_month=None, total_holidays=0):
    """Build a payroll data row for a sales employee (commission-only, no attendance deductions).
    Fixed salary employees get attendance-based salary instead of commission."""
    if emp_type == 'inhouse':
        submissions_qs = BankSubmission.objects.filter(employee=emp, year=year, month=month)
        adjustments = PayrollAdjustment.objects.filter(employee=emp, year=year, month=month)
    else:
        submissions_qs = BankSubmission.objects.filter(remote_employee=emp, year=year, month=month)
        adjustments = PayrollAdjustment.objects.filter(remote_employee=emp, year=year, month=month)

    emp_currency = emp.currency if hasattr(emp, 'currency') else 'AED'
    bank_counts = {s.bank_id: s.submission_count for s in submissions_qs}
    bank_counts_list = [bank_counts.get(b.id, 0) for b in banks]

    if emp_currency == 'INR':
        pairs = [(bank_counts.get(b.id, 0), b.charge_for_currency('INR')) for b in banks]
        commission, _ = _calc_inr_tiered_commission(pairs)
    else:
        commission = sum(
            bank_counts.get(b.id, 0) * float(b.per_account_charge)
            for b in banks
        )

    incentives = float(
        adjustments.filter(adjustment_type='incentive').aggregate(total=Sum('amount'))['total'] or 0
    )
    reductions = float(
        adjustments.filter(adjustment_type='reduction').aggregate(total=Sum('amount'))['total'] or 0
    )

    # display_type is based on location field (same logic as the Salary Setup page)
    if emp_type == 'inhouse':
        display_type = 'inhouse'
    else:
        display_type = 'inhouse' if (emp.location and emp.location.lower() == 'inhouse') else 'remote'

    # Fixed salary: attendance-based salary, bank counts kept for record only.
    # Present days are computed on-the-fly from raw records (a single punch / call
    # on a workday counts as Present) so the calculation reflects the *current*
    # fixed-salary rule even if stored summaries / attendance_status fields are
    # stale from before the toggle was flipped.
    if emp.is_fixed_salary:
        if days_in_month is None:
            days_in_month = calendar.monthrange(year, month)[1]
        salary = float(emp.salary) if emp.salary else 0.0
        daily_rate = salary / days_in_month if salary > 0 else 0.0
        total_working_days = days_in_month - total_holidays

        if emp_type == 'inhouse':
            present_days = AttendanceRecord.objects.filter(
                employee=emp, date__year=year, date__month=month,
                first_in__isnull=False,
            ).count()
        else:
            call_records = RemoteCallRecord.objects.filter(
                employee=emp, date__year=year, date__month=month,
            ).only('answered_calls', 'no_answered', 'busy', 'failed')
            present_days = sum(
                1 for r in call_records
                if (r.answered_calls or 0) + (r.no_answered or 0)
                   + (r.busy or 0) + (r.failed or 0) > 0
            )

        absent_days = max(0, total_working_days - present_days)
        deduction = daily_rate * absent_days
        base_salary = round(salary - deduction, 2)
        net_payroll = round(base_salary + incentives - reductions, 2)

        return {
            'employee': emp,
            'employee_type': emp_type,
            'display_type': display_type,
            'currency': emp.currency,
            'is_fixed_salary': True,
            'salary': round(salary, 2),
            'daily_rate': round(daily_rate, 2),
            'absent_days': absent_days,
            'deduction': round(deduction, 2),
            'bank_counts_list': bank_counts_list,
            'commission': round(commission, 2),
            'incentives': round(incentives, 2),
            'reductions': round(reductions, 2),
            'net_payroll': net_payroll,
        }

    # Attendance-based: salary scales with actual attendance, plus any commission.
    # Half days count as 0.5 of a working day. Statuses are recomputed on-the-fly
    # from raw call records so the math is correct even if stored statuses are
    # stale from a previous fixed-salary configuration.
    if getattr(emp, 'payroll_type', 'attendance') == 'attendance' and emp.salary:
        if days_in_month is None:
            days_in_month = calendar.monthrange(year, month)[1]
        salary = float(emp.salary)
        daily_rate = salary / days_in_month if salary > 0 else 0.0
        total_working_days = days_in_month - total_holidays

        if emp_type == 'inhouse':
            present_days = AttendanceRecord.objects.filter(
                employee=emp, date__year=year, date__month=month,
                first_in__isnull=False,
            ).count()
            half_days = 0  # in-house half-day detection lives in the report layer
        else:
            # Skip Sundays + custom holidays: they're already excluded from
            # total_working_days, and calculate_attendance_status() returns
            # 'present' for any Sunday with talk_duration, which would
            # double-discount them and understate absent days.
            holiday_dates = set(
                Holiday.objects.filter(
                    date__year=year, date__month=month
                ).values_list('date', flat=True)
            )
            call_records = list(RemoteCallRecord.objects.filter(
                employee=emp, date__year=year, date__month=month,
            ))
            present_days = 0
            half_days = 0
            for r in call_records:
                if r.date.weekday() == 6 or r.date in holiday_dates:
                    continue
                status = r.calculate_attendance_status()
                if status == 'present':
                    present_days += 1
                elif status == 'half_day':
                    half_days += 1

        effective_present = present_days + (half_days * 0.5)
        absent_days = max(0, total_working_days - effective_present)
        deduction = daily_rate * absent_days
        base_salary = round(salary - deduction, 2)
        net_payroll = round(base_salary + commission + incentives - reductions, 2)

        return {
            'employee': emp,
            'employee_type': emp_type,
            'display_type': display_type,
            'currency': emp.currency,
            'is_fixed_salary': False,
            'is_attendance_based': True,
            'salary': round(salary, 2),
            'daily_rate': round(daily_rate, 2),
            'present_days': present_days,
            'half_days': half_days,
            'absent_days': round(absent_days, 1),
            'deduction': round(deduction, 2),
            'base_salary': base_salary,
            'bank_counts_list': bank_counts_list,
            'commission': round(commission, 2),
            'incentives': round(incentives, 2),
            'reductions': round(reductions, 2),
            'net_payroll': net_payroll,
        }

    net_payroll = commission + incentives - reductions
    return {
        'employee': emp,
        'employee_type': emp_type,
        'display_type': display_type,
        'is_fixed_salary': False,
        'currency': emp.currency,
        'bank_counts_list': bank_counts_list,
        'commission': round(commission, 2),
        'incentives': round(incentives, 2),
        'reductions': round(reductions, 2),
        'net_payroll': round(net_payroll, 2),
    }


def _build_section_totals(rows):
    """Sum net, incentives, reductions, commission across a list of payroll rows."""
    return (
        round(sum(r['net_payroll'] for r in rows), 2),
        round(sum(r['incentives'] for r in rows), 2),
        round(sum(r['reductions'] for r in rows), 2),
        round(sum(r.get('commission', 0) for r in rows), 2),
    )


# ============================================
# Main Dashboard
# ============================================

@login_required
@user_passes_test(superuser_required, login_url='/report/')
def payroll_dashboard(request):
    """Payroll dashboard showing Admin and Sales sections."""
    selected_month, selected_year = get_selected_month_year(request)
    _, days_in_month = calendar.monthrange(selected_year, selected_month)
    month_start = datetime.date(selected_year, selected_month, 1)
    month_end = datetime.date(selected_year, selected_month, days_in_month)

    total_holidays = _count_holidays(selected_year, selected_month, days_in_month)

    # Fetch active banks for sales spreadsheet
    banks = list(Bank.objects.filter(is_active=True).order_by('name'))
    banks_json = json.dumps([
        {
            'id': b.id,
            'name': b.name,
            'rate': float(b.per_account_charge),
            'inr_rate': float(b.inr_per_account_charge) if b.inr_per_account_charge else None,
        }
        for b in banks
    ])

    # --- Admin section (in-house Admin dept) ---
    admin_employees = Employee.objects.filter(
        department='Admin', is_active=True
    ).order_by('name')
    admin_data = [
        _get_inhouse_payroll_row(emp, selected_year, selected_month, month_start, month_end, total_holidays)
        for emp in admin_employees
    ]
    total_admin, admin_incentives_total, admin_reductions_total, _ = _build_section_totals(admin_data)

    # --- Sales section: in-house Sales employees (commission-only) ---
    sales_inhouse_employees = Employee.objects.filter(
        department='Sales', is_active=True
    ).order_by('name')
    sales_inhouse_data = [
        _get_sales_payroll_row(emp, selected_year, selected_month, 'inhouse', banks, days_in_month, total_holidays)
        for emp in sales_inhouse_employees
    ]
    total_sales_inhouse, _, _, _ = _build_section_totals(sales_inhouse_data)

    # --- Sales section: remote employees (commission-only) ---
    # Exclude remote employees whose tcr_id already appears as an in-house employee
    # (same person tracked in both tables) to prevent duplicates
    all_inhouse_tcr_ids = set(
        Employee.objects.filter(is_active=True)
        .exclude(tcr_id='')
        .values_list('tcr_id', flat=True)
    )
    remote_employees = RemoteEmployee.objects.filter(is_active=True).order_by('name')
    if all_inhouse_tcr_ids:
        remote_employees = remote_employees.exclude(tcr_id__in=all_inhouse_tcr_ids)
    remote_data = [
        _get_sales_payroll_row(emp, selected_year, selected_month, 'remote', banks, days_in_month, total_holidays)
        for emp in remote_employees
    ]
    total_remote, _, _, _ = _build_section_totals(remote_data)

    # Combined sales data: salary-bearing rows (fixed / attendance-based) first,
    # then pure commission rows
    def _has_base_salary(r):
        return r.get('is_fixed_salary') or r.get('is_attendance_based')

    all_sales_data = sorted(
        sales_inhouse_data + remote_data,
        key=lambda r: (0 if _has_base_salary(r) else 1, r['employee'].name.lower())
    )

    # Combined Sales totals
    all_sales_rows = sales_inhouse_data + remote_data
    total_sales, sales_incentives_total, sales_reductions_total, _ = _build_section_totals(all_sales_rows)
    total_sales_aed = round(sum(r['net_payroll'] for r in all_sales_rows if r.get('currency', 'AED') == 'AED'), 2)
    total_sales_inr = round(sum(r['net_payroll'] for r in all_sales_rows if r.get('currency', 'AED') == 'INR'), 2)
    # Tfoot totals split by currency. Salary-bearing rows surface net_payroll
    # (they may have salary + commission), pure commission rows surface commission only.
    def _sales_tfoot(rows, currency):
        return round(
            sum(r.get('commission', 0) for r in rows if not _has_base_salary(r) and r.get('currency', 'AED') == currency) +
            sum(r['net_payroll'] for r in rows if _has_base_salary(r) and r.get('currency', 'AED') == currency),
            2
        )
    total_sales_commission_aed = _sales_tfoot(all_sales_rows, 'AED')
    total_sales_commission_inr = _sales_tfoot(all_sales_rows, 'INR')

    grand_total = round(total_admin + total_sales, 2)

    # --- Section 3: Employee Deduction Spreadsheet ---
    _month_names = {
        1: 'Jan', 2: 'Feb', 3: 'Mar', 4: 'Apr', 5: 'May', 6: 'Jun',
        7: 'Jul', 8: 'Aug', 9: 'Sep', 10: 'Oct', 11: 'Nov', 12: 'Dec',
    }
    _DED_COLS = ['advance', 'visa_status_change', 'clawback', 'leave_deduction', 'late_deduction', 'other_deduction']
    _ADD_COLS = ['last_month_balance', 'paid_leave']
    _ALL_CATS = _DED_COLS + _ADD_COLS
    target_idx = selected_year * 12 + (selected_month - 1)

    # Load all DeductionEntry records; build active-this-month map and all-entries list
    active_by_emp = _defaultdict(list)
    all_deductions_list = []
    for entry in DeductionEntry.objects.select_related('employee', 'remote_employee').order_by('-created_at'):
        emp = entry.employee or entry.remote_employee
        emp_type = 'inhouse' if entry.employee else 'remote'
        start_idx = entry.start_year * 12 + (entry.start_month - 1)
        if start_idx <= target_idx < start_idx + entry.split_months:
            active_by_emp[(emp_type, emp.id)].append(entry)
        end_y, end_m = entry.end_month_year()
        all_deductions_list.append({
            'id': entry.id,
            'employee': emp,
            'employee_type': emp_type,
            'category_display': entry.get_category_display(),
            'entry_type': entry.entry_type,
            'total_amount': float(entry.total_amount),
            'split_months': entry.split_months,
            'installment_amount': float(entry.installment_amount),
            'start_month_name': _month_names[entry.start_month],
            'start_year': entry.start_year,
            'end_month_name': _month_names[end_m],
            'end_year': end_y,
            'note': entry.note,
            'created_at': entry.created_at.strftime('%d %b %Y'),
        })

    # Pre-load monthly summaries for auto leave/late deductions (avoid N+1)
    inhouse_summaries = {
        s.employee_id: s
        for s in MonthlySummary.objects.filter(year=selected_year, month=selected_month)
    }

    section3_rows = []
    for emp in Employee.objects.filter(is_active=True).order_by('department', 'name'):
        cat = {c: 0.0 for c in _ALL_CATS}
        for ded_entry in active_by_emp.get(('inhouse', emp.id), []):
            cat[ded_entry.category] = round(cat[ded_entry.category] + float(ded_entry.installment_amount), 2)
        # Auto-compute leave/late from attendance (skip for performance-based payroll)
        summary = inhouse_summaries.get(emp.id)
        if summary and emp.salary and emp.payroll_type != 'performance':
            daily = float(emp.salary) / days_in_month
            cat['leave_deduction'] = round(daily * (summary.leave_days or 0), 2)
            cat['late_deduction'] = round(daily * ((summary.late_days or 0) // 3) * 0.5, 2)
        # Admin employees: leave/late are display-only — already deducted in attendance
        # payroll (net_payroll). Exclude them from total_deductions to avoid double-counting
        # in the Final Summary.
        if emp.department == 'Admin':
            ded_cols_for_total = [c for c in _DED_COLS if c not in ('leave_deduction', 'late_deduction')]
        else:
            ded_cols_for_total = _DED_COLS
        total_ded = round(sum(cat[c] for c in ded_cols_for_total), 2)
        total_add = round(sum(cat[c] for c in _ADD_COLS), 2)
        row = {'employee': emp, 'employee_type': 'inhouse', 'is_inhouse': True}
        row.update(cat)
        row.update({'total_deductions': total_ded, 'total_additions': total_add, 'net': round(total_add - total_ded, 2)})
        section3_rows.append(row)

    for emp in RemoteEmployee.objects.filter(is_active=True).order_by('name'):
        cat = {c: 0.0 for c in _ALL_CATS}
        for ded_entry in active_by_emp.get(('remote', emp.id), []):
            cat[ded_entry.category] = round(cat[ded_entry.category] + float(ded_entry.installment_amount), 2)
        total_ded = round(sum(cat[c] for c in _DED_COLS), 2)
        total_add = round(sum(cat[c] for c in _ADD_COLS), 2)
        row = {'employee': emp, 'employee_type': 'remote', 'is_inhouse': False}
        row.update(cat)
        row.update({'total_deductions': total_ded, 'total_additions': total_add, 'net': round(total_add - total_ded, 2)})
        section3_rows.append(row)

    s3_total_ded = round(sum(r['total_deductions'] for r in section3_rows), 2)
    s3_total_add = round(sum(r['total_additions'] for r in section3_rows), 2)
    s3_net = round(s3_total_add - s3_total_ded, 2)

    # All employees for Add modal dropdown
    all_employees_json = json.dumps([
        {'id': emp.id, 'name': emp.name, 'type': 'inhouse', 'dept': emp.department or ''}
        for emp in Employee.objects.filter(is_active=True).order_by('name')
    ] + [
        {'id': emp.id, 'name': emp.name, 'type': 'remote', 'dept': 'Remote'}
        for emp in RemoteEmployee.objects.filter(is_active=True).order_by('name')
    ])

    # --- Section 5: Final Summary ---
    # Build lookup dicts keyed by (emp_type, emp_id) for payroll and deductions
    payroll_by_emp = {}
    for row in admin_data:
        payroll_by_emp[('inhouse', row['employee'].id)] = row
    for row in all_sales_data:
        payroll_by_emp[(row['employee_type'], row['employee'].id)] = row

    ded_by_emp = {}
    for row in section3_rows:
        ded_by_emp[(row['employee_type'], row['employee'].id)] = row

    final_rows = []
    for emp in Employee.objects.filter(is_active=True).order_by('department', 'name'):
        key = ('inhouse', emp.id)
        p = payroll_by_emp.get(key)
        d = ded_by_emp.get(key)
        payroll_net = p['net_payroll'] if p else 0.0
        total_ded = d['total_deductions'] if d else 0.0
        total_add = d['total_additions'] if d else 0.0
        final_salary = round(payroll_net - total_ded + total_add, 2)
        final_rows.append({
            'employee': emp,
            'employee_type': 'inhouse',
            'department': emp.department or 'In-House',
            'currency': emp.currency,
            'payroll_net': payroll_net,
            'total_deductions': total_ded,
            'total_additions': total_add,
            'final_salary': final_salary,
        })

    for emp in remote_employees:
        key = ('remote', emp.id)
        p = payroll_by_emp.get(key)
        d = ded_by_emp.get(key)
        payroll_net = p['net_payroll'] if p else 0.0
        total_ded = d['total_deductions'] if d else 0.0
        total_add = d['total_additions'] if d else 0.0
        final_salary = round(payroll_net - total_ded + total_add, 2)
        final_rows.append({
            'employee': emp,
            'employee_type': 'remote',
            'department': 'Remote',
            'currency': emp.currency,
            'payroll_net': payroll_net,
            'total_deductions': total_ded,
            'total_additions': total_add,
            'final_salary': final_salary,
        })

    final_total_aed = round(sum(r['final_salary'] for r in final_rows if r.get('currency', 'AED') == 'AED'), 2)
    final_total_inr = round(sum(r['final_salary'] for r in final_rows if r.get('currency', 'AED') == 'INR'), 2)
    final_total = round(sum(r['final_salary'] for r in final_rows), 2)

    # Grand total split by currency
    all_rows = admin_data + all_sales_data
    grand_total_aed = round(sum(r['net_payroll'] for r in all_rows if r.get('currency', 'AED') == 'AED'), 2)
    grand_total_inr = round(sum(r['net_payroll'] for r in all_rows if r.get('currency', 'AED') == 'INR'), 2)

    context = {
        'selected_month': selected_month,
        'selected_year': selected_year,
        'month_name': MONTH_NAMES[selected_month],
        'months': MONTH_CHOICES,
        'years': YEAR_RANGE,
        'total_holidays': total_holidays,
        # Admin
        'admin_data': admin_data,
        'total_admin': total_admin,
        'admin_incentives_total': admin_incentives_total,
        'admin_reductions_total': admin_reductions_total,
        # Sales (spreadsheet)
        'banks': banks,
        'banks_json': banks_json,
        'INR_COMMISSION_THRESHOLD': INR_COMMISSION_THRESHOLD,
        'INR_OVERFLOW_RATE': int(INR_OVERFLOW_RATE),
        'all_sales_data': all_sales_data,
        'total_sales_inhouse': total_sales_inhouse,
        'total_remote': total_remote,
        'total_sales': total_sales,
        'total_sales_aed': total_sales_aed,
        'total_sales_inr': total_sales_inr,
        'total_sales_commission_aed': total_sales_commission_aed,
        'total_sales_commission_inr': total_sales_commission_inr,
        'sales_incentives_total': sales_incentives_total,
        'sales_reductions_total': sales_reductions_total,
        # Deductions & Additions (Section 3)
        'section3_rows': section3_rows,
        'all_deductions_list': all_deductions_list,
        's3_total_ded': s3_total_ded,
        's3_total_add': s3_total_add,
        's3_net': s3_net,
        'all_employees_json': all_employees_json,
        # Final Summary
        'final_rows': final_rows,
        'final_total': final_total,
        'final_total_aed': final_total_aed,
        'final_total_inr': final_total_inr,
        # Grand total
        'grand_total': grand_total,
        'grand_total_aed': grand_total_aed,
        'grand_total_inr': grand_total_inr,
    }

    return render(request, 'payroll/dashboard.html', context)


# ============================================
# Bank Management
# ============================================

@login_required
@user_passes_test(superuser_required, login_url='/report/')
def manage_banks(request):
    """Bank management page — list/add/edit/deactivate banks."""
    banks = Bank.objects.all().order_by('name')
    return render(request, 'payroll/banks.html', {'banks': banks})


@login_required
@user_passes_test(superuser_required, login_url='/report/')
@require_http_methods(["GET", "POST"])
def banks_api(request):
    """GET: list active banks. POST: add a new bank."""
    if request.method == 'GET':
        banks = Bank.objects.filter(is_active=True).order_by('name')
        data = [{'id': b.id, 'name': b.name, 'per_account_charge': float(b.per_account_charge)} for b in banks]
        return JsonResponse({'success': True, 'banks': data})

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    name = data.get('name', '').strip()
    per_account_charge = data.get('per_account_charge')
    inr_per_account_charge = data.get('inr_per_account_charge')

    if not name or per_account_charge is None:
        return JsonResponse({'success': False, 'error': 'Name and per_account_charge are required'}, status=400)

    if Bank.objects.filter(name__iexact=name).exists():
        return JsonResponse({'success': False, 'error': 'A bank with this name already exists'}, status=400)

    try:
        inr_charge = Decimal(str(inr_per_account_charge)) if inr_per_account_charge not in (None, '', 0, '0') else None
        bank = Bank.objects.create(
            name=name,
            per_account_charge=Decimal(str(per_account_charge)),
            inr_per_account_charge=inr_charge,
        )
    except (ValueError, TypeError) as e:
        return JsonResponse({'success': False, 'error': f'Invalid data: {e}'}, status=400)

    logger.info("Bank added: %s (AED %s/account) by %s", bank.name, bank.per_account_charge, request.user.username)
    return JsonResponse({'success': True, 'bank': {
        'id': bank.id, 'name': bank.name,
        'per_account_charge': float(bank.per_account_charge),
        'inr_per_account_charge': float(bank.inr_per_account_charge) if bank.inr_per_account_charge else None,
        'is_active': bank.is_active,
    }})


@login_required
@user_passes_test(superuser_required, login_url='/report/')
@require_http_methods(["POST"])
def bank_detail_api(request, bank_id):
    """Update or toggle a bank."""
    try:
        bank = Bank.objects.get(id=bank_id)
    except Bank.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Bank not found'}, status=404)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    action = data.get('action')

    if action == 'update':
        name = data.get('name', '').strip()
        per_account_charge = data.get('per_account_charge')
        inr_per_account_charge = data.get('inr_per_account_charge')
        if not name or per_account_charge is None:
            return JsonResponse({'success': False, 'error': 'Name and charge are required'}, status=400)
        # Check uniqueness excluding self
        if Bank.objects.filter(name__iexact=name).exclude(id=bank_id).exists():
            return JsonResponse({'success': False, 'error': 'Another bank with this name already exists'}, status=400)
        try:
            bank.name = name
            bank.per_account_charge = Decimal(str(per_account_charge))
            bank.inr_per_account_charge = (
                Decimal(str(inr_per_account_charge))
                if inr_per_account_charge not in (None, '', 0, '0')
                else None
            )
            bank.save()
        except (ValueError, TypeError) as e:
            return JsonResponse({'success': False, 'error': f'Invalid data: {e}'}, status=400)
        logger.info("Bank updated: %s by %s", bank.name, request.user.username)
        return JsonResponse({'success': True, 'bank': {
            'id': bank.id, 'name': bank.name,
            'per_account_charge': float(bank.per_account_charge),
            'inr_per_account_charge': float(bank.inr_per_account_charge) if bank.inr_per_account_charge else None,
            'is_active': bank.is_active,
        }})

    elif action == 'toggle':
        bank.is_active = not bank.is_active
        bank.save()
        logger.info("Bank %s: %s by %s", 'activated' if bank.is_active else 'deactivated', bank.name, request.user.username)
        return JsonResponse({'success': True, 'is_active': bank.is_active})

    return JsonResponse({'success': False, 'error': 'Unknown action'}, status=400)


# ============================================
# API: Bank Submissions
# ============================================

@login_required
@user_passes_test(superuser_required, login_url='/report/')
def get_submissions(request, emp_type, employee_id):
    """Get bank submissions for an employee for a specific month."""
    try:
        year = int(request.GET.get('year', datetime.datetime.now().year))
        month = int(request.GET.get('month', datetime.datetime.now().month))
    except (ValueError, TypeError):
        return JsonResponse({'success': False, 'error': 'Invalid year/month'})

    if emp_type == 'inhouse':
        try:
            employee = Employee.objects.get(id=employee_id)
        except Employee.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        submissions_qs = BankSubmission.objects.filter(
            employee=employee, year=year, month=month
        ).select_related('bank')
    else:
        try:
            employee = RemoteEmployee.objects.get(id=employee_id)
        except RemoteEmployee.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        submissions_qs = BankSubmission.objects.filter(
            remote_employee=employee, year=year, month=month
        ).select_related('bank')

    submission_map = {s.bank_id: s.submission_count for s in submissions_qs}
    banks = list(Bank.objects.filter(is_active=True).order_by('name'))
    emp_currency = employee.currency if hasattr(employee, 'currency') else 'AED'

    if emp_currency == 'INR':
        pairs = [(submission_map.get(b.id, 0), b.charge_for_currency('INR')) for b in banks]
        total_commission, per_bank_commissions = _calc_inr_tiered_commission(pairs)
        data = [
            {
                'bank_id': b.id,
                'bank_name': b.name,
                'per_account_charge': float(b.charge_for_currency('INR')),
                'currency': 'INR',
                'submission_count': submission_map.get(b.id, 0),
                'commission': round(per_bank_commissions[i], 2),
            }
            for i, b in enumerate(banks)
        ]
    else:
        data = []
        total_commission = 0.0
        for bank in banks:
            count = submission_map.get(bank.id, 0)
            charge = float(bank.per_account_charge)
            commission = round(count * charge, 2)
            total_commission += commission
            data.append({
                'bank_id': bank.id,
                'bank_name': bank.name,
                'per_account_charge': charge,
                'currency': emp_currency,
                'submission_count': count,
                'commission': commission,
            })

    return JsonResponse({
        'success': True,
        'employee_name': employee.name,
        'currency': emp_currency,
        'banks': data,
        'total_commission': round(total_commission, 2),
    })


@login_required
@user_passes_test(superuser_required, login_url='/report/')
@require_http_methods(["POST"])
def save_submissions(request):
    """Save bank submission counts for an employee for a month."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    emp_type = data.get('emp_type')
    employee_id = data.get('employee_id')
    year = data.get('year')
    month = data.get('month')
    submissions = data.get('submissions', {})  # {bank_id: count}

    if not all([emp_type, employee_id, year, month]):
        return JsonResponse({'success': False, 'error': 'Missing required fields'}, status=400)

    if emp_type == 'inhouse':
        try:
            employee = Employee.objects.get(id=employee_id)
        except Employee.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        fk_kwargs = {'employee': employee}
    else:
        try:
            employee = RemoteEmployee.objects.get(id=employee_id)
        except RemoteEmployee.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        fk_kwargs = {'remote_employee': employee}

    emp_currency = employee.currency if hasattr(employee, 'currency') else 'AED'

    try:
        year = int(year)
        month = int(month)
    except (ValueError, TypeError):
        return JsonResponse({'success': False, 'error': 'Invalid year/month'}, status=400)

    for bank_id_str, count_val in submissions.items():
        try:
            bank_id = int(bank_id_str)
            count = int(count_val)
            bank = Bank.objects.get(id=bank_id, is_active=True)
        except (ValueError, TypeError, Bank.DoesNotExist):
            continue

        if count <= 0:
            BankSubmission.objects.filter(bank=bank, year=year, month=month, **fk_kwargs).delete()
        else:
            BankSubmission.objects.update_or_create(
                bank=bank, year=year, month=month, **fk_kwargs,
                defaults={'submission_count': count},
            )

    # Re-calculate total commission after saving using the same tiered logic as the dashboard
    total_commission = _get_commission(year, month, currency=emp_currency, **{
        'employee' if emp_type == 'inhouse' else 'remote_employee': employee
    })

    logger.info("Bank submissions saved for %s by %s", employee.name, request.user.username)
    return JsonResponse({'success': True, 'total_commission': round(total_commission, 2)})


# ============================================
# API: In-house Employee Adjustments
# ============================================

@login_required
@user_passes_test(superuser_required, login_url='/report/')
def get_adjustments(request, employee_id):
    """Get adjustments for an in-house employee for a specific month."""
    try:
        year = int(request.GET.get('year', datetime.datetime.now().year))
        month = int(request.GET.get('month', datetime.datetime.now().month))
    except (ValueError, TypeError):
        return JsonResponse({'success': False, 'error': 'Invalid year/month'})

    try:
        employee = Employee.objects.get(id=employee_id)
    except Employee.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)

    adjustments = PayrollAdjustment.objects.filter(
        employee=employee, year=year, month=month
    )
    data = [{
        'id': adj.id,
        'type': adj.adjustment_type,
        'amount': float(adj.amount),
        'reason': adj.reason,
        'created_at': adj.created_at.strftime('%Y-%m-%d %H:%M'),
    } for adj in adjustments]

    return JsonResponse({
        'success': True,
        'employee_name': employee.name,
        'adjustments': data,
    })


@login_required
@user_passes_test(superuser_required, login_url='/report/')
@require_http_methods(["POST"])
def add_adjustment(request):
    """Add a new adjustment for an in-house employee."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    employee_id = data.get('employee_id')
    year = data.get('year')
    month = data.get('month')
    adjustment_type = data.get('type')
    amount = data.get('amount')
    reason = data.get('reason', '')

    if not all([employee_id, year, month, adjustment_type, amount]):
        return JsonResponse({'success': False, 'error': 'Missing required fields'}, status=400)

    if adjustment_type not in ('incentive', 'reduction'):
        return JsonResponse({'success': False, 'error': 'Invalid adjustment type'}, status=400)

    try:
        employee = Employee.objects.get(id=employee_id)
    except Employee.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)

    try:
        adjustment = PayrollAdjustment.objects.create(
            employee=employee,
            year=int(year),
            month=int(month),
            adjustment_type=adjustment_type,
            amount=Decimal(str(amount)),
            reason=reason,
        )
    except (ValueError, TypeError) as e:
        return JsonResponse({'success': False, 'error': f'Invalid data: {e}'}, status=400)

    logger.info(
        "Payroll adjustment added: %s %s %s by %s",
        employee.name, adjustment_type, amount, request.user.username
    )
    return JsonResponse({
        'success': True,
        'message': 'Adjustment added successfully',
        'adjustment': {
            'id': adjustment.id,
            'type': adjustment.adjustment_type,
            'amount': float(adjustment.amount),
            'reason': adjustment.reason,
        },
    })


# ============================================
# API: Remote Employee Adjustments
# ============================================

@login_required
@user_passes_test(superuser_required, login_url='/report/')
def get_remote_adjustments(request, employee_id):
    """Get adjustments for a remote employee for a specific month."""
    try:
        year = int(request.GET.get('year', datetime.datetime.now().year))
        month = int(request.GET.get('month', datetime.datetime.now().month))
    except (ValueError, TypeError):
        return JsonResponse({'success': False, 'error': 'Invalid year/month'})

    try:
        employee = RemoteEmployee.objects.get(id=employee_id)
    except RemoteEmployee.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)

    adjustments = PayrollAdjustment.objects.filter(
        remote_employee=employee, year=year, month=month
    )
    data = [{
        'id': adj.id,
        'type': adj.adjustment_type,
        'amount': float(adj.amount),
        'reason': adj.reason,
        'created_at': adj.created_at.strftime('%Y-%m-%d %H:%M'),
    } for adj in adjustments]

    return JsonResponse({
        'success': True,
        'employee_name': employee.name,
        'adjustments': data,
    })


@login_required
@user_passes_test(superuser_required, login_url='/report/')
@require_http_methods(["POST"])
def add_remote_adjustment(request):
    """Add a new adjustment for a remote employee."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    employee_id = data.get('employee_id')
    year = data.get('year')
    month = data.get('month')
    adjustment_type = data.get('type')
    amount = data.get('amount')
    reason = data.get('reason', '')

    if not all([employee_id, year, month, adjustment_type, amount]):
        return JsonResponse({'success': False, 'error': 'Missing required fields'}, status=400)

    if adjustment_type not in ('incentive', 'reduction'):
        return JsonResponse({'success': False, 'error': 'Invalid adjustment type'}, status=400)

    try:
        employee = RemoteEmployee.objects.get(id=employee_id)
    except RemoteEmployee.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)

    try:
        adjustment = PayrollAdjustment.objects.create(
            remote_employee=employee,
            year=int(year),
            month=int(month),
            adjustment_type=adjustment_type,
            amount=Decimal(str(amount)),
            reason=reason,
        )
    except (ValueError, TypeError) as e:
        return JsonResponse({'success': False, 'error': f'Invalid data: {e}'}, status=400)

    logger.info(
        "Remote payroll adjustment added: %s %s %s by %s",
        employee.name, adjustment_type, amount, request.user.username
    )
    return JsonResponse({
        'success': True,
        'message': 'Adjustment added successfully',
        'adjustment': {
            'id': adjustment.id,
            'type': adjustment.adjustment_type,
            'amount': float(adjustment.amount),
            'reason': adjustment.reason,
        },
    })


# ============================================
# API: Recalculate Summaries
# ============================================

@login_required
@user_passes_test(superuser_required, login_url='/report/')
@require_http_methods(["POST"])
def recalculate_summaries(request):
    """Trigger recalculation of monthly summaries for the selected month/year."""
    try:
        data = json.loads(request.body)
        year = int(data.get('year'))
        month = int(data.get('month'))
    except (json.JSONDecodeError, TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Invalid year/month'}, status=400)

    if not (1 <= month <= 12) or not (2000 <= year <= 2099):
        return JsonResponse({'success': False, 'error': 'Invalid year/month'}, status=400)

    call_command('recalculate_summaries', year, month, verbosity=0)
    logger.info("Payroll summaries recalculated for %s/%s by %s", year, month, request.user.username)
    return JsonResponse({'success': True})


# ============================================
# API: Delete Adjustment (both types)
# ============================================

# ============================================
# Upload: Bank Submissions XLSX
# ============================================

@login_required
@user_passes_test(superuser_required, login_url='/report/')
@require_http_methods(["POST"])
def upload_submissions(request):
    """
    Parse a Target vs Achieved XLSX file and upsert BankSubmission records.

    Expected columns (any order, header detection by keyword):
      - Id      → employee TCR ID (optional; used when present)
      - Agent   → employee name (fallback lookup when Id is absent/empty)
      - {BankName} Ach → achieved submission count for each bank

    Each row = one employee. The achieved count for each bank is read directly
    from the "{Bank} Ach" column (Total Ach is ignored).
    Employees are looked up by tcr_id (via Id column) or name (via Agent column)
    across both Employee and RemoteEmployee.
    """
    uploaded_file = request.FILES.get('file')
    if not uploaded_file:
        return JsonResponse({'success': False, 'error': 'No file provided'}, status=400)

    try:
        year = int(request.POST.get('year'))
        month = int(request.POST.get('month'))
    except (TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Invalid year/month'}, status=400)

    if not (1 <= month <= 12) or not (2000 <= year <= 2099):
        return JsonResponse({'success': False, 'error': 'Invalid year/month range'}, status=400)

    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(uploaded_file.read()), read_only=True, data_only=True)
        ws = wb.active
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Could not read file: {e}'}, status=400)

    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return JsonResponse({'success': False, 'error': 'File is empty'}, status=400)

    # Detect columns from header row
    raw_headers = [str(c).strip() if c else '' for c in rows[0]]
    headers_lower = [h.lower() for h in raw_headers]

    agent_col = id_col = None
    # {bank_name_lower: col_index} for "{Bank} Ach" columns (excluding "total ach")
    bank_ach_cols = {}

    for i, h in enumerate(headers_lower):
        if h == 'id':
            id_col = i
        elif h == 'agent':
            agent_col = i
        elif h.endswith(' ach') and not h.startswith('total'):
            bank_name = raw_headers[i][:-4].strip()  # strip " Ach" suffix
            bank_ach_cols[bank_name.lower()] = (i, bank_name)

    if agent_col is None:
        return JsonResponse(
            {'success': False, 'error': 'Could not find "Agent" column in header'},
            status=400
        )
    if not bank_ach_cols:
        return JsonResponse(
            {'success': False, 'error': 'Could not find any "{Bank} Ach" columns in header'},
            status=400
        )

    # Preload active banks for fast lookup (case-insensitive)
    bank_map = {b.name.lower(): b for b in Bank.objects.filter(is_active=True)}

    matched = 0
    unmatched_agents = set()
    unmatched_banks = set()

    for row in rows[1:]:
        if not any(row):
            continue

        agent_name = str(row[agent_col]).strip() if row[agent_col] else ''
        tcr_id = str(row[id_col]).strip() if (id_col is not None and row[id_col]) else ''

        if not agent_name and not tcr_id:
            continue

        # Employee lookup: prefer tcr_id, fall back to name
        employee = remote_employee = None
        if tcr_id:
            employee = Employee.objects.filter(tcr_id=tcr_id, is_active=True).first()
            if not employee:
                remote_employee = RemoteEmployee.objects.filter(tcr_id=tcr_id, is_active=True).first()
        if not employee and not remote_employee and agent_name:
            employee = Employee.objects.filter(name__iexact=agent_name, is_active=True).first()
            if not employee:
                remote_employee = RemoteEmployee.objects.filter(name__iexact=agent_name, is_active=True).first()

        if not employee and not remote_employee:
            unmatched_agents.add(tcr_id or agent_name)
            continue

        fk_kwargs = {'employee': employee} if employee else {'remote_employee': remote_employee}

        for bank_lower, (col_idx, bank_display_name) in bank_ach_cols.items():
            try:
                count = int(row[col_idx]) if row[col_idx] is not None else 0
            except (ValueError, TypeError):
                count = 0

            bank = bank_map.get(bank_lower)
            if not bank:
                unmatched_banks.add(bank_display_name)
                continue

            if count <= 0:
                BankSubmission.objects.filter(bank=bank, year=year, month=month, **fk_kwargs).delete()
            else:
                BankSubmission.objects.update_or_create(
                    bank=bank, year=year, month=month, **fk_kwargs,
                    defaults={'submission_count': count},
                )
            matched += 1

    logger.info(
        "Submission upload for %d/%d: %d saved, %d unmatched agents, %d unmatched banks by %s",
        year, month, matched, len(unmatched_agents), len(unmatched_banks), request.user.username,
    )

    return JsonResponse({
        'success': True,
        'stats': {
            'matched': matched,
            'unmatched_agents': sorted(unmatched_agents),
            'unmatched_banks': sorted(unmatched_banks),
        },
    })


# ============================================
# Payroll Employee Database
# ============================================

@login_required
@user_passes_test(superuser_required, login_url='/report/')
def payroll_employees(request):
    """Payroll employee database — salary, currency, designation for all employees.

    Section assignment is driven by the location field:
      location = 'inhouse' → in-house section (Employee table only)
      location = 'remote'  → remote section (RemoteEmployee records whose
                              location is not 'inhouse')
    """
    inhouse_employees = Employee.objects.filter(is_active=True).order_by('department', 'name')

    # Only show remote employees whose location is not marked as 'inhouse'
    remote_employees = (
        RemoteEmployee.objects.filter(is_active=True)
        .exclude(location__iexact='inhouse')
        .order_by('name')
    )

    return render(request, 'payroll/employees.html', {
        'inhouse_employees': inhouse_employees,
        'remote_employees': remote_employees,
    })


@login_required
@user_passes_test(superuser_required, login_url='/report/')
@require_http_methods(["POST"])
def payroll_employee_update(request, emp_type, employee_id):
    """Update payroll-relevant fields (salary, currency, designation, department) for an employee."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    if emp_type == 'inhouse':
        try:
            emp = Employee.objects.get(id=employee_id)
        except Employee.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
    elif emp_type == 'remote':
        try:
            emp = RemoteEmployee.objects.get(id=employee_id)
        except RemoteEmployee.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
    else:
        return JsonResponse({'success': False, 'error': 'Invalid employee type'}, status=400)

    if 'salary' in data:
        val = data['salary']
        try:
            emp.salary = Decimal(str(val)) if val not in (None, '', 0) else None
        except (ValueError, TypeError):
            return JsonResponse({'success': False, 'error': 'Invalid salary value'}, status=400)

    if 'currency' in data:
        if data['currency'] not in ('AED', 'INR'):
            return JsonResponse({'success': False, 'error': 'Currency must be AED or INR'}, status=400)
        emp.currency = data['currency']

    if 'designation' in data:
        emp.designation = str(data['designation']).strip() or None

    if 'department' in data:
        dept = str(data['department']).strip()
        if dept not in ('Sales', 'Admin', ''):
            return JsonResponse({'success': False, 'error': 'Invalid department'}, status=400)
        emp.department = dept or None

    if 'payroll_type' in data:
        pt = str(data['payroll_type']).strip()
        if pt not in ('attendance', 'performance'):
            return JsonResponse({'success': False, 'error': 'Invalid payroll type'}, status=400)
        emp.payroll_type = pt

    if 'is_fixed_salary' in data:
        emp.is_fixed_salary = bool(data['is_fixed_salary'])

    emp.save()
    logger.info("Payroll employee updated: %s (%s) by %s", emp.name, emp_type, request.user.username)
    return JsonResponse({
        'success': True,
        'salary': float(emp.salary) if emp.salary else None,
        'currency': emp.currency,
        'designation': emp.designation or '',
        'department': emp.department or '',
        'payroll_type': emp.payroll_type,
        'is_fixed_salary': emp.is_fixed_salary,
    })


@login_required
@user_passes_test(superuser_required, login_url='/report/')
@require_http_methods(["POST"])
def delete_adjustment(request, adjustment_id):
    """Delete an adjustment (works for both in-house and remote)."""
    try:
        adjustment = PayrollAdjustment.objects.get(id=adjustment_id)
    except PayrollAdjustment.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Adjustment not found'}, status=404)

    logger.info(
        "Payroll adjustment deleted: id=%s by %s",
        adjustment_id, request.user.username
    )
    adjustment.delete()
    return JsonResponse({'success': True, 'message': 'Adjustment deleted'})


# ============================================
# API: Deductions & Additions
# ============================================

@login_required
@user_passes_test(superuser_required, login_url='/report/')
@require_http_methods(["POST"])
def add_deduction(request):
    """Create a new DeductionEntry (deduction or addition) for an employee."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    emp_type = data.get('emp_type')
    employee_id = data.get('employee_id')
    category = data.get('category')
    total_amount = data.get('total_amount')
    split_months = data.get('split_months', 1)
    start_year = data.get('start_year')
    start_month = data.get('start_month')
    note = data.get('note', '')

    if not all([emp_type, employee_id, category, total_amount, start_year, start_month]):
        return JsonResponse({'success': False, 'error': 'Missing required fields'}, status=400)

    valid_categories = {c[0] for c in DEDUCTION_CATEGORY_CHOICES}
    if category not in valid_categories:
        return JsonResponse({'success': False, 'error': 'Invalid category'}, status=400)

    if emp_type == 'inhouse':
        try:
            employee = Employee.objects.get(id=employee_id)
        except Employee.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        fk_kwargs = {'employee': employee}
    elif emp_type == 'remote':
        try:
            employee = RemoteEmployee.objects.get(id=employee_id)
        except RemoteEmployee.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        fk_kwargs = {'remote_employee': employee}
    else:
        return JsonResponse({'success': False, 'error': 'Invalid employee type'}, status=400)

    try:
        entry = DeductionEntry.objects.create(
            **fk_kwargs,
            category=category,
            total_amount=Decimal(str(total_amount)),
            split_months=max(1, int(split_months)),
            start_year=int(start_year),
            start_month=int(start_month),
            note=note,
        )
    except (ValueError, TypeError) as e:
        return JsonResponse({'success': False, 'error': f'Invalid data: {e}'}, status=400)

    logger.info(
        "DeductionEntry added: %s %s %s by %s",
        employee.name, category, total_amount, request.user.username,
    )
    return JsonResponse({'success': True})


@login_required
@user_passes_test(superuser_required, login_url='/report/')
@require_http_methods(["POST"])
def delete_deduction_entry(request, deduction_id):
    """Delete a DeductionEntry."""
    try:
        entry = DeductionEntry.objects.get(id=deduction_id)
    except DeductionEntry.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Deduction not found'}, status=404)

    logger.info("DeductionEntry deleted: id=%s by %s", deduction_id, request.user.username)
    entry.delete()
    return JsonResponse({'success': True})


@login_required
@user_passes_test(superuser_required, login_url='/report/')
@require_http_methods(["GET"])
def autofill_deduction(request):
    """Return auto-computed leave/late deduction amounts for an in-house employee."""
    try:
        employee_id = int(request.GET.get('employee_id'))
        year = int(request.GET.get('year'))
        month = int(request.GET.get('month'))
    except (TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Invalid parameters'}, status=400)

    try:
        employee = Employee.objects.get(id=employee_id)
    except Employee.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)

    _, days_in_month = calendar.monthrange(year, month)
    summary = MonthlySummary.objects.filter(employee=employee, year=year, month=month).first()

    if not summary or not employee.salary or employee.payroll_type == 'performance':
        return JsonResponse({'success': True, 'leave_amount': 0.0, 'late_amount': 0.0})

    salary = float(employee.salary)
    daily_rate = salary / days_in_month
    absent_days = summary.leave_days or 0
    late_days = summary.late_days or 0
    late_half_days = late_days // 3

    return JsonResponse({
        'success': True,
        'leave_amount': round(daily_rate * absent_days, 2),
        'late_amount': round(daily_rate * late_half_days * 0.5, 2),
        'daily_rate': round(daily_rate, 2),
        'absent_days': absent_days,
        'late_days': late_days,
        'late_half_days': late_half_days,
    })


# ============================================
# Payslip Download
# ============================================

def _amount_in_words(amount):
    """Convert a numeric amount (integer AED) to English words for payslips."""
    ones = [
        '', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine',
        'Ten', 'Eleven', 'Twelve', 'Thirteen', 'Fourteen', 'Fifteen', 'Sixteen',
        'Seventeen', 'Eighteen', 'Nineteen',
    ]
    tens_words = ['', '', 'Twenty', 'Thirty', 'Forty', 'Fifty', 'Sixty', 'Seventy', 'Eighty', 'Ninety']

    def below_thousand(n):
        if n == 0:
            return ''
        if n < 20:
            return ones[n]
        if n < 100:
            rem = ones[n % 10]
            return tens_words[n // 10] + (' ' + rem if rem else '')
        rem = below_thousand(n % 100)
        return ones[n // 100] + ' Hundred' + (' ' + rem if rem else '')

    n = int(round(abs(amount)))
    if n == 0:
        return 'Zero Only'

    parts = []
    if n >= 1_000_000:
        parts.append(below_thousand(n // 1_000_000) + ' Million')
        n %= 1_000_000
    if n >= 1000:
        parts.append(below_thousand(n // 1000) + ' Thousand')
        n %= 1000
    if n > 0:
        parts.append(below_thousand(n))
    return ' '.join(parts) + ' Only'


@login_required
@user_passes_test(superuser_required, login_url='/report/')
def download_payslip(request, emp_type, emp_id):
    """Render a printable HTML payslip for one employee for the selected month."""
    selected_month, selected_year = get_selected_month_year(request)
    days_in_month = calendar.monthrange(selected_year, selected_month)[1]
    month_start = datetime.date(selected_year, selected_month, 1)
    month_end = datetime.date(selected_year, selected_month, days_in_month)
    month_name = MONTH_NAMES[selected_month]

    if emp_type == 'inhouse':
        emp = get_object_or_404(Employee, id=emp_id)
    else:
        emp = get_object_or_404(RemoteEmployee, id=emp_id)

    salary = float(emp.salary) if emp.salary else 0.0

    # --- Payroll figures ---
    if emp_type == 'inhouse' and emp.department == 'Admin':
        total_holidays = _count_holidays(selected_year, selected_month, days_in_month)
        payroll = _get_inhouse_payroll_row(
            emp, selected_year, selected_month, month_start, month_end, total_holidays
        )
        daily_rate = payroll['daily_rate']
        absent_days = payroll['absent_days']
        half_days = payroll['half_days']
        late_half_days = payroll['late_half_days']
        incentives = payroll['incentives']
        commission = payroll['commission']
        reductions = payroll['reductions']
        annual_leave_compensation = payroll['annual_leave_compensation']
        annual_leave_extra_deduction = payroll['annual_leave_extra_deduction']
        total_deduction_days = payroll['total_deduction_days']
        absent_days_display = round(total_deduction_days, 2)
        # Fixed earnings from DB (no proration)
        basic_full = round(salary * 0.40, 2)
        allowance_full = round(salary * 0.60, 2)
        # Attendance-based deduction components
        att_leave_ded = round(daily_rate * (absent_days + half_days * 0.5)
                              - annual_leave_compensation + annual_leave_extra_deduction, 2)
        att_late_ded = round(daily_rate * (late_half_days * 0.5), 2)
    else:
        banks = list(Bank.objects.filter(is_active=True).order_by('name'))
        payroll = _get_sales_payroll_row(
            emp, selected_year, selected_month, emp_type, banks
        )
        incentives = payroll['incentives']
        commission = payroll['commission']
        reductions = payroll['reductions']
        absent_days_display = 0
        basic_full = 0.0
        allowance_full = 0.0
        att_leave_ded = 0.0
        att_late_ded = 0.0

    # --- Active DeductionEntry records for this month ---
    target_idx = selected_year * 12 + (selected_month - 1)
    if emp_type == 'inhouse':
        deduction_entries = list(DeductionEntry.objects.filter(employee=emp))
    else:
        deduction_entries = list(DeductionEntry.objects.filter(remote_employee=emp))

    advance_ded = 0.0
    leave_ded_manual = 0.0
    late_ded_manual = 0.0
    other_ded_manual = 0.0
    additions = 0.0
    for entry in deduction_entries:
        start_idx = entry.start_year * 12 + (entry.start_month - 1)
        if start_idx <= target_idx < start_idx + entry.split_months:
            amt = float(entry.installment_amount)
            if entry.category == 'advance':
                advance_ded += amt
            elif entry.category in ('paid_leave', 'last_month_balance'):
                additions += amt
            elif entry.category == 'leave_deduction':
                leave_ded_manual += amt
            elif entry.category == 'late_deduction':
                late_ded_manual += amt
            elif entry.category in ('visa_status_change', 'clawback', 'other_deduction'):
                other_ded_manual += amt

    advance_ded = round(advance_ded, 2)
    additions = round(additions, 2)
    # Combine attendance-based with manual entries; include PA reductions in other
    leave_deduction = round(att_leave_ded + leave_ded_manual, 2)
    late_deduction = round(att_late_ded + late_ded_manual, 2)
    other_ded = round(other_ded_manual + reductions, 2)

    incentives_commission = round(incentives + commission, 2)
    total_earnings = round(basic_full + allowance_full + incentives_commission + additions, 2)
    total_deductions = round(leave_deduction + late_deduction + advance_ded + other_ded, 2)
    net_salary = round(total_earnings - total_deductions, 2)

    salary_words = _amount_in_words(net_salary) if net_salary > 0 else 'Zero Only'

    # --- Render HTML payslip ---
    logger.info("Payslip viewed for %s %s/%s by %s", emp.name, selected_month, selected_year, request.user.username)
    return render(request, 'payroll/payslip.html', {
        'emp': emp,
        'month_name': month_name,
        'selected_month': selected_month,
        'selected_year': selected_year,
        'days_in_month': days_in_month,
        'absent_days_display': absent_days_display,
        'salary': salary,
        'basic_full': basic_full,
        'allowance_full': allowance_full,
        'incentives_commission': incentives_commission,
        'additions': additions,
        'leave_deduction': leave_deduction,
        'late_deduction': late_deduction,
        'advance_ded': advance_ded,
        'other_ded': other_ded,
        'total_earnings': total_earnings,
        'total_deductions': total_deductions,
        'net_salary': net_salary,
        'salary_words': salary_words,
    })
