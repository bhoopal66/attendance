"""
Upload views for attendance data.
Handles Excel uploads for in-house employees and CSV uploads for remote employees.
"""

import logging
import os
import re
from datetime import timedelta, datetime

import pandas as pd
from bs4 import BeautifulSoup
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.management import call_command
from django.db import transaction
from django.shortcuts import redirect, render

from ..models import (
    AttendanceRecord, EarlyLeaveRequest, Employee, EmployeeIDAlias,
    RemoteCallRecord, RemoteEmployee, RemoteEmployeeIDAlias,
)
from .utils import parse_duration, superuser_required

logger = logging.getLogger('attendance')

# Allowed file extensions for uploads
ALLOWED_ATTENDANCE_EXTENSIONS = {'.xls', '.xlsx'}
ALLOWED_REMOTE_EXTENSIONS = {'.csv'}


def parse_html_excel(file_content):
    """
    Parse HTML-based Excel file (.xls exported from web systems).
    Returns a tuple of (dataframe, extracted_date).
    """
    if isinstance(file_content, bytes):
        content = file_content.decode('utf-8', errors='ignore')
    else:
        content = file_content

    soup = BeautifulSoup(content, 'html.parser')

    # Extract date from the Detail2 table (contains date range)
    extracted_date = None
    detail_table = soup.find('table', class_='Detail2')
    if detail_table:
        text = detail_table.get_text()
        date_match = re.search(r'(\d{4}-\d{2}-\d{2})\s+\d{2}:\d{2}:\d{2}\s*-', text)
        if date_match:
            extracted_date = date_match.group(1)

    # Find the Punch_Report table which contains the actual data
    punch_table = soup.find('table', class_='Punch_Report')
    if not punch_table:
        raise ValueError("Could not find attendance data table in the file")

    # Get all td elements - each row has 11 columns
    tds = punch_table.find_all('td')
    rows = []
    for i in range(0, len(tds), 11):
        if i + 11 <= len(tds):
            row = [td.get_text(strip=True) for td in tds[i:i + 11]]
            rows.append(row)

    if not rows:
        raise ValueError("No attendance data found in the file")

    columns = [
        'Index', 'Person ID', 'Name', 'Department', 'Position',
        'Gender', 'Date', 'Day Of Week', 'Timetable', 'First-In', 'Last-Out'
    ]
    df = pd.DataFrame(rows, columns=columns)
    return df, extracted_date


def is_html_excel(file_content):
    """Check if the file content is HTML-based Excel."""
    try:
        if isinstance(file_content, bytes):
            content_start = file_content[:500].decode('utf-8', errors='ignore').lower()
        else:
            content_start = file_content[:500].lower()
        return '<html' in content_start or '<!doctype html' in content_start
    except (UnicodeDecodeError, AttributeError):
        return False


def _validate_file_extension(filename, allowed_extensions):
    """Validate that a file has an allowed extension."""
    _, ext = os.path.splitext(filename)
    ext = ext.lower()
    if ext not in allowed_extensions:
        allowed = ', '.join(sorted(allowed_extensions))
        raise ValueError(f"Invalid file type '{ext}'. Allowed: {allowed}")
    return ext


def _lookup_or_create_employee(person_id, name):
    """
    Look up an in-house employee using a tiered strategy. Returns (employee, was_created).

    Employees with a tcr_id are considered confirmed — they are matched first
    (by name or person_id) and their person_id is updated if it changed.
    The fallback tiers (name matching, alias history, create new) only apply
    to employees WITHOUT a tcr_id.
    """
    # Tier 0: Exact match on person_id + name (fast path, any employee)
    employee = Employee.objects.filter(person_id=person_id, name=name).first()
    if employee:
        return employee, False

    # --- Confirmed employees (tcr_id is set) take priority ---

    # Tier 1a: Confirmed employee matched by name
    confirmed_by_name = Employee.objects.filter(name=name, tcr_id__isnull=False, is_active=True)
    if confirmed_by_name.count() == 1:
        employee = confirmed_by_name.first()
        if employee.person_id != person_id:
            old_id = employee.person_id
            EmployeeIDAlias.objects.get_or_create(employee=employee, person_id=old_id)
            employee.person_id = person_id
            employee.save(update_fields=['person_id', 'updated_at'])
            logger.info("Updated person_id for %s (tcr_id=%s): %s → %s", name, employee.tcr_id, old_id, person_id)
        return employee, False

    # Tier 1b: Confirmed employee matched by person_id (name may have changed)
    confirmed_by_pid = Employee.objects.filter(person_id=person_id, tcr_id__isnull=False, is_active=True).first()
    if confirmed_by_pid:
        return confirmed_by_pid, False

    # --- Fallback tiers for employees WITHOUT tcr_id ---

    # Tier 2: Active employee with same name (no tcr_id)
    active_by_name = Employee.objects.filter(name=name, is_active=True, tcr_id__isnull=True)
    count = active_by_name.count()

    if count == 1:
        employee = active_by_name.first()
        if employee.person_id != person_id:
            old_id = employee.person_id
            EmployeeIDAlias.objects.get_or_create(employee=employee, person_id=old_id)
            employee.person_id = person_id
            employee.save(update_fields=['person_id', 'updated_at'])
            logger.info("Updated person_id for %s: %s → %s (old ID archived)", name, old_id, person_id)
        return employee, False
    elif count > 1:
        employee = active_by_name.filter(person_id=person_id).first()
        if employee:
            return employee, False
        return active_by_name.order_by('-updated_at').first(), False

    # Tier 3: Check alias history (active employees only — inactive IDs are released)
    alias = EmployeeIDAlias.objects.filter(
        person_id=person_id, employee__is_active=True
    ).select_related('employee').first()
    if alias:
        employee = alias.employee
        old_id = employee.person_id
        EmployeeIDAlias.objects.get_or_create(employee=employee, person_id=old_id)
        employee.person_id = person_id
        employee.save(update_fields=['person_id', 'updated_at'])
        logger.info("Matched %s via alias person_id %s → updated to %s", employee.name, person_id, person_id)
        return employee, False

    # Tier 4: Create new
    employee = Employee.objects.create(person_id=person_id, name=name)
    logger.info("Created new employee: %s (person_id=%s)", name, person_id)
    return employee, True


def _strip_csv_suffix(name):
    """Strip parenthetical suffix from CSV names like 'Bridget (TCR Team)' → 'Bridget'."""
    return re.sub(r'\s*\(.*\)\s*$', '', name).strip()


def _find_inhouse_by_name(name):
    """Find a unique active in-house employee matching the given name (with or without CSV suffix)."""
    match = Employee.objects.filter(name=name, is_active=True)
    if match.count() == 1:
        return match.first()
    # Try stripping parenthetical suffix: "Bridget (TCR Team)" → "Bridget"
    base_name = _strip_csv_suffix(name)
    if base_name != name:
        match = Employee.objects.filter(name__iexact=base_name, is_active=True)
        if match.count() == 1:
            return match.first()
    return None


def _lookup_or_create_remote_employee(extension_id, name):
    """
    Look up a remote employee using a 5-tier strategy.
    Returns (employee, was_created).
    """
    # Tier 1: Exact match
    employee = RemoteEmployee.objects.filter(extension_id=extension_id, name=name).first()
    if employee:
        return employee, False

    # Tier 2: Active employee with same name
    active_by_name = RemoteEmployee.objects.filter(name=name, is_active=True)
    count = active_by_name.count()

    if count == 1:
        employee = active_by_name.first()
        if employee.extension_id != extension_id:
            old_id = employee.extension_id
            RemoteEmployeeIDAlias.objects.get_or_create(employee=employee, extension_id=old_id)
            employee.extension_id = extension_id
            employee.save(update_fields=['extension_id', 'updated_at'])
            logger.info("Updated extension_id for %s: %s → %s (old ID archived)", name, old_id, extension_id)
        return employee, False
    elif count > 1:
        employee = active_by_name.filter(extension_id=extension_id).first()
        if employee:
            return employee, False
        return active_by_name.order_by('-updated_at').first(), False

    # Tier 3: Check alias history (active employees only)
    alias = RemoteEmployeeIDAlias.objects.filter(
        extension_id=extension_id, employee__is_active=True
    ).select_related('employee').first()
    if alias:
        employee = alias.employee
        old_id = employee.extension_id
        RemoteEmployeeIDAlias.objects.get_or_create(employee=employee, extension_id=old_id)
        employee.extension_id = extension_id
        employee.save(update_fields=['extension_id', 'updated_at'])
        logger.info("Matched %s via alias extension_id %s → updated to %s", employee.name, extension_id, extension_id)
        return employee, False

    # Tier 4: Cross-reference via in-house employee to find already-linked remote
    inhouse = _find_inhouse_by_name(name)
    if inhouse and inhouse.tcr_id:
        linked_remote = RemoteEmployee.objects.filter(tcr_id=inhouse.tcr_id).first()
        if linked_remote:
            old_id = linked_remote.extension_id
            RemoteEmployeeIDAlias.objects.get_or_create(employee=linked_remote, extension_id=old_id)
            linked_remote.extension_id = extension_id
            linked_remote.save(update_fields=['extension_id', 'updated_at'])
            logger.info("Matched '%s' via in-house tcr_id %s → updated extension %s → %s",
                        name, inhouse.tcr_id, old_id, extension_id)
            return linked_remote, False

    # Tier 4b: Match by base name within existing remote employees
    # Handles name variations like "Bridget (TCR Team)" vs "Bridget ( TCR Team )"
    base_name = _strip_csv_suffix(name)
    if base_name != name:
        base_matches = RemoteEmployee.objects.filter(
            name__icontains=base_name, is_active=True
        )
        if base_matches.count() == 1:
            employee = base_matches.first()
            if employee.extension_id != extension_id:
                old_id = employee.extension_id
                RemoteEmployeeIDAlias.objects.get_or_create(employee=employee, extension_id=old_id)
                employee.extension_id = extension_id
                employee.name = name  # Update to latest name from CSV
                employee.save(update_fields=['extension_id', 'name', 'updated_at'])
                logger.info("Matched '%s' via base name '%s' → updated extension %s → %s",
                            name, base_name, old_id, extension_id)
            return employee, False

    # Tier 5: Create new — auto-link to in-house employee if one exists
    employee = RemoteEmployee.objects.create(extension_id=extension_id, name=name)

    if inhouse:
        # Copy shared fields from in-house record
        if inhouse.tcr_id:
            employee.tcr_id = inhouse.tcr_id
        for field in ('department', 'location', 'team', 'currency', 'is_fixed_salary',
                      'salary', 'designation', 'joining_date', 'payroll_type'):
            inhouse_val = getattr(inhouse, field, None)
            if inhouse_val is not None:
                setattr(employee, field, inhouse_val)
        employee.save()
        logger.info("Auto-copied fields from in-house '%s' to new remote employee", inhouse.name)

    logger.info("Created new remote employee: %s (ext=%s)", name, extension_id)
    return employee, True


def _merge_with_approved_times(employee, date_val, fi_time, lo_time):
    """
    Merge biometric times with approved on-duty request times.
    Takes earliest check-in and latest checkout.
    """
    approved_request = EarlyLeaveRequest.objects.filter(
        employee=employee,
        request_date=date_val,
        status='approved'
    ).first()

    if not approved_request:
        return fi_time, lo_time

    if approved_request.approved_first_in:
        if fi_time:
            fi_time = min(fi_time, approved_request.approved_first_in)
        else:
            fi_time = approved_request.approved_first_in

    if approved_request.approved_last_out:
        if lo_time:
            lo_time = max(lo_time, approved_request.approved_last_out)
        else:
            lo_time = approved_request.approved_last_out

    return fi_time, lo_time


def _calculate_work_duration(fi_time, lo_time):
    """Calculate work duration from first-in and last-out times."""
    if fi_time and lo_time:
        duration_seconds = (
            lo_time.hour * 3600 + lo_time.minute * 60 + lo_time.second
        ) - (
            fi_time.hour * 3600 + fi_time.minute * 60 + fi_time.second
        )
        return timedelta(seconds=max(0, duration_seconds))
    return timedelta(0)


@login_required
@user_passes_test(superuser_required, login_url='/report/')
def upload_file(request):
    """Handle Excel file upload for in-house attendance data."""
    if request.method != 'POST' or not request.FILES.get('file'):
        return render(request, 'attendance/upload.html')

    excel_file = request.FILES['file']
    selected_date_str = request.POST.get('date')

    try:
        _validate_file_extension(excel_file.name, ALLOWED_ATTENDANCE_EXTENSIONS)
    except ValueError as e:
        messages.error(request, str(e))
        return redirect('upload')

    try:
        file_content = excel_file.read()
        excel_file.seek(0)

        if is_html_excel(file_content):
            df, extracted_date = parse_html_excel(file_content)
            if not selected_date_str and extracted_date:
                selected_date_str = extracted_date
            elif not selected_date_str:
                messages.error(request, 'Could not extract date from file. Please select a date manually.')
                return redirect('upload')
        else:
            if not selected_date_str:
                messages.error(request, 'Please select a date.')
                return redirect('upload')

            _, ext = os.path.splitext(excel_file.name)
            engine = "xlrd" if ext.lower() == ".xls" else "openpyxl"
            df = pd.read_excel(excel_file, engine=engine)

        df.replace("-", pd.NA, inplace=True)
        date_val = pd.to_datetime(selected_date_str).date()

        df["First-In"] = pd.to_datetime(
            selected_date_str + " " + df["First-In"].astype(str),
            errors="coerce"
        )
        df["Last-Out"] = pd.to_datetime(
            selected_date_str + " " + df["Last-Out"].astype(str),
            errors="coerce"
        )

        grouped = df.groupby(["Person ID", "Name"])
        processed_count = 0
        new_employees = []

        with transaction.atomic():
            for (person_id, name), group in grouped:
                employee, created = _lookup_or_create_employee(person_id, name)
                if created:
                    new_employees.append(name)

                first_in = group["First-In"].min()
                last_out = group["Last-Out"].max()

                fi_time = first_in.time() if not pd.isna(first_in) else None
                lo_time = last_out.time() if not pd.isna(last_out) else None

                fi_time, lo_time = _merge_with_approved_times(employee, date_val, fi_time, lo_time)
                duration = _calculate_work_duration(fi_time, lo_time)

                AttendanceRecord.objects.update_or_create(
                    employee=employee,
                    date=date_val,
                    defaults={
                        'first_in': fi_time,
                        'last_out': lo_time,
                        'work_duration': duration
                    }
                )
                processed_count += 1

        call_command('recalculate_summaries', date_val.year, date_val.month, verbosity=0)

        logger.info(
            "Attendance upload completed: %d employees for %s by %s",
            processed_count, selected_date_str, request.user.username
        )
        messages.success(
            request,
            f'File uploaded and processed successfully! '
            f'{processed_count} employees updated for {selected_date_str}.'
        )
        if new_employees:
            names = ', '.join(new_employees)
            messages.warning(
                request,
                f'{len(new_employees)} new employee record(s) were auto-created: {names}. '
                f'If these are existing employees with a new machine ID, use the '
                f'Employee Directory to merge the duplicate records.'
            )
        return redirect('upload')

    except ValueError as e:
        logger.warning("Upload validation error: %s", e)
        messages.error(request, f'Error processing file: {e}')
        return redirect('upload')
    except Exception:
        logger.exception("Unexpected error during attendance upload")
        messages.error(request, 'An unexpected error occurred while processing the file.')
        return redirect('upload')


@login_required
@user_passes_test(superuser_required, login_url='/report/')
def upload_remote_call_stats(request):
    """Handle CSV upload for remote team call statistics."""
    if request.method != 'POST' or not request.FILES.get('remote_file'):
        return redirect('upload')

    csv_file = request.FILES['remote_file']
    selected_date_str = request.POST.get('remote_date')

    if not selected_date_str:
        messages.error(request, 'Please select a date for remote call statistics.')
        return redirect('upload')

    try:
        _validate_file_extension(csv_file.name, ALLOWED_REMOTE_EXTENSIONS)
    except ValueError as e:
        messages.error(request, str(e))
        return redirect('upload')

    try:
        df = pd.read_csv(csv_file)
        selected_date = pd.to_datetime(selected_date_str).date()

        processed_count = 0
        new_employees = []

        with transaction.atomic():
            for _, row in df.iterrows():
                extension_col = row.get('Extension', '')

                if str(extension_col).strip().lower() == 'total':
                    continue

                if '-' not in str(extension_col):
                    continue

                parts = str(extension_col).split('-', 1)
                extension_id = parts[0].strip()
                name = parts[1].strip() if len(parts) > 1 else 'Unknown'

                employee, created = _lookup_or_create_remote_employee(extension_id, name)
                if created:
                    new_employees.append(name)

                answered = int(row.get('Answered', 0) or 0)
                no_answered = int(row.get('No Answered', 0) or 0)
                busy = int(row.get('Busy', 0) or 0)
                failed = int(row.get('Failed', 0) or 0)
                voicemail = int(row.get('Voicemail', 0) or 0)

                ring_duration = parse_duration(row.get('Total Ring Duration', ''))
                talk_duration = parse_duration(row.get('Total Talk Duration', ''))

                RemoteCallRecord.objects.update_or_create(
                    employee=employee,
                    date=selected_date,
                    defaults={
                        'answered_calls': answered,
                        'no_answered': no_answered,
                        'busy': busy,
                        'failed': failed,
                        'voicemail': voicemail,
                        'total_ring_duration': ring_duration,
                        'total_talk_duration': talk_duration,
                    }
                )
                processed_count += 1

        call_command('recalculate_summaries', selected_date.year, selected_date.month, remote=True, verbosity=0)

        logger.info(
            "Remote upload completed: %d employees for %s by %s",
            processed_count, selected_date_str, request.user.username
        )
        messages.success(
            request,
            f'Remote call statistics uploaded! Processed {processed_count} employees.'
        )
        if new_employees:
            names = ', '.join(new_employees)
            messages.warning(
                request,
                f'{len(new_employees)} new remote employee record(s) were auto-created: {names}. '
                f'If these are existing employees with a new extension, use the '
                f'Employee Directory to merge the duplicate records.'
            )
        return redirect('upload')

    except ValueError as e:
        logger.warning("Remote upload validation error: %s", e)
        messages.error(request, f'Error processing remote file: {e}')
        return redirect('upload')
    except Exception:
        logger.exception("Unexpected error during remote upload")
        messages.error(request, 'An unexpected error occurred while processing the file.')
        return redirect('upload')


@login_required
@user_passes_test(superuser_required, login_url='/report/')
def upload_remote_monthly(request):
    """Handle CSV upload for monthly remote team call statistics (per-day breakdown)."""
    if request.method != 'POST' or not request.FILES.get('remote_monthly_file'):
        return redirect('upload')

    csv_file = request.FILES['remote_monthly_file']
    selected_month_str = request.POST.get('remote_month')  # "YYYY-MM" format

    if not selected_month_str:
        messages.error(request, 'Please select a month for monthly remote call statistics.')
        return redirect('upload')

    try:
        _validate_file_extension(csv_file.name, ALLOWED_REMOTE_EXTENSIONS)
    except ValueError as e:
        messages.error(request, str(e))
        return redirect('upload')

    try:
        selected_year = int(selected_month_str.split('-')[0])

        df = pd.read_csv(csv_file)

        if 'Date' not in df.columns:
            messages.error(request, 'Invalid file format. Monthly CSV must have a "Date" column.')
            return redirect('upload')

        processed_count = 0
        new_employees = []
        dates_processed = set()

        with transaction.atomic():
            for _, row in df.iterrows():
                date_str = str(row.get('Date', '')).strip()
                extension_col = row.get('Extension', '')

                # Skip total rows (daily totals and grand total)
                if date_str.lower() == 'total':
                    continue
                if str(extension_col).strip().lower() == 'total':
                    continue
                if '-' not in str(extension_col):
                    continue

                # Parse date: "Mar. 1" + year -> date object
                try:
                    date_text = date_str.replace('.', '').strip()
                    record_date = datetime.strptime(f"{date_text} {selected_year}", "%b %d %Y").date()
                except ValueError:
                    logger.warning("Could not parse date '%s', skipping row", date_str)
                    continue

                dates_processed.add(record_date)

                parts = str(extension_col).split('-', 1)
                extension_id = parts[0].strip()
                name = parts[1].strip() if len(parts) > 1 else 'Unknown'

                employee, created = _lookup_or_create_remote_employee(extension_id, name)
                if created:
                    new_employees.append(name)

                answered = int(row.get('Answered', 0) or 0)
                no_answered = int(row.get('No Answered', 0) or 0)
                busy = int(row.get('Busy', 0) or 0)
                failed = int(row.get('Failed', 0) or 0)
                voicemail = int(row.get('Voicemail', 0) or 0)

                ring_duration = parse_duration(row.get('Total Ring Duration', ''))
                talk_duration = parse_duration(row.get('Total Talk Duration', ''))

                RemoteCallRecord.objects.update_or_create(
                    employee=employee,
                    date=record_date,
                    defaults={
                        'answered_calls': answered,
                        'no_answered': no_answered,
                        'busy': busy,
                        'failed': failed,
                        'voicemail': voicemail,
                        'total_ring_duration': ring_duration,
                        'total_talk_duration': talk_duration,
                    }
                )
                processed_count += 1

        # Recalculate summaries for all months that had data
        months_processed = {(d.year, d.month) for d in dates_processed}
        for year, month in months_processed:
            call_command('recalculate_summaries', year, month, remote=True, verbosity=0)

        logger.info(
            "Monthly remote upload completed: %d records across %d days for %s by %s",
            processed_count, len(dates_processed), selected_month_str, request.user.username
        )
        messages.success(
            request,
            f'Monthly remote call statistics uploaded! '
            f'Processed {processed_count} records across {len(dates_processed)} days.'
        )
        if new_employees:
            names = ', '.join(new_employees)
            messages.warning(
                request,
                f'{len(new_employees)} new remote employee record(s) were auto-created: {names}. '
                f'If these are existing employees with a new extension, use the '
                f'Employee Directory to merge the duplicate records.'
            )
        return redirect('upload')

    except ValueError as e:
        logger.warning("Monthly remote upload validation error: %s", e)
        messages.error(request, f'Error processing file: {e}')
        return redirect('upload')
    except Exception:
        logger.exception("Unexpected error during monthly remote upload")
        messages.error(request, 'An unexpected error occurred while processing the file.')
        return redirect('upload')
