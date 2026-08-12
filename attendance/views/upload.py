"""
Upload views for attendance data.
Handles Excel uploads for in-house employees and CSV uploads for remote employees.
"""

import io
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
from .utils import parse_duration, section_required

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


def parse_daily_report_excel(file_content):
    """
    Parse a multi-day HTML-based Daily Report Excel file.
    These use a Daily_Report table with 20 columns including an embedded Date column.
    Returns a DataFrame with columns: Person ID, Name, Date, First-In, Last-Out.
    """
    if isinstance(file_content, bytes):
        content = file_content.decode('utf-8', errors='ignore')
    else:
        content = file_content

    soup = BeautifulSoup(content, 'html.parser')

    daily_table = soup.find('table', class_='Daily_Report')
    if not daily_table:
        raise ValueError("Could not find Daily_Report table in the file. Is this the correct multi-day report format?")

    tds = daily_table.find_all('td')
    cols_per_row = 20
    rows = []
    for i in range(0, len(tds), cols_per_row):
        if i + cols_per_row <= len(tds):
            row = [td.get_text(strip=True) for td in tds[i:i + cols_per_row]]
            rows.append(row)

    if not rows:
        raise ValueError("No attendance data found in the Daily_Report table")

    all_columns = [
        'Index', 'Person ID', 'Name', 'Department', 'Position', 'Gender',
        'Date', 'Week', 'Timetable', 'First-In', 'Last-Out',
        'Work', 'OT', 'Attended', 'Late', 'Early', 'Absent', 'Leave', 'Status', 'Records'
    ]
    df = pd.DataFrame(rows, columns=all_columns)
    return df[['Person ID', 'Name', 'Date', 'First-In', 'Last-Out']]


def is_daily_report_excel(file_content):
    """Check if an HTML-based Excel file is a multi-day Daily_Report format."""
    if isinstance(file_content, bytes):
        content = file_content.decode('utf-8', errors='ignore')
    else:
        content = file_content
    return 'Daily_Report' in content


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


def _clean_id(value):
    """
    Normalize a Person ID / Extension ID coming from a spreadsheet.

    Pandas often reads numeric IDs as floats (12345 -> 12345.0); naively
    str()-ing those produces "12345.0" which never matches the stored
    CharField "12345". HTML cells can also carry NBSP / extra whitespace.

    Biometric machines sometimes export the same numeric ID in two formats
    ("8" and "00000008"). Strip leading zeros from purely numeric IDs so
    both forms normalize to the same value and lookups stay consistent.
    """
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, float):
        if pd.isna(value):
            return None
        if value.is_integer():
            return str(int(value))
        return str(value)
    if isinstance(value, int):
        return str(value)
    s = str(value).replace('\xa0', ' ').strip()
    if not s or s.lower() in ('nan', 'none', '<na>'):
        return None
    # Trailing ".0" from accidental float stringification upstream
    if re.fullmatch(r'\d+\.0+', s):
        s = s.split('.', 1)[0]
    # Strip leading zeros from purely numeric IDs ("00000008" → "8")
    if re.fullmatch(r'\d+', s):
        s = str(int(s))
    return s


def _clean_name(value):
    """Normalize a name from a spreadsheet: NBSP -> space, collapse whitespace."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    s = str(value).replace('\xa0', ' ').strip()
    if not s or s.lower() in ('nan', 'none', '<na>'):
        return None
    return re.sub(r'\s+', ' ', s)


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

    # Tier 1b: Confirmed employee matched by person_id AND name.
    # If only person_id matches but the name differs, treat it as a reassigned
    # machine ID (e.g., leaver's slot given to a new hire) and fall through to
    # create a fresh record. Admin can merge later if it really was the same person.
    confirmed_by_pid = Employee.objects.filter(
        person_id=person_id, name=name, tcr_id__isnull=False, is_active=True
    ).first()
    if confirmed_by_pid:
        return confirmed_by_pid, False
    name_mismatch_pid = Employee.objects.filter(
        person_id=person_id, tcr_id__isnull=False, is_active=True
    ).exclude(name=name).first()
    if name_mismatch_pid:
        logger.info(
            "person_id %s is held by confirmed employee '%s' but upload name is '%s' — "
            "treating as reassigned ID; new record will be created",
            person_id, name_mismatch_pid.name, name
        )

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

    # Tier 3: Check alias history (active employees only — inactive IDs are released).
    # Only reuse the alias if the name on the upload matches the alias-owning
    # employee's current name. If the name differs, this is a reassigned ID — fall
    # through to create a new record.
    alias = EmployeeIDAlias.objects.filter(
        person_id=person_id, employee__is_active=True, employee__name=name
    ).select_related('employee').first()
    if alias:
        employee = alias.employee
        old_id = employee.person_id
        EmployeeIDAlias.objects.get_or_create(employee=employee, person_id=old_id)
        employee.person_id = person_id
        employee.save(update_fields=['person_id', 'updated_at'])
        logger.info("Matched %s via alias person_id %s → updated to %s", employee.name, person_id, person_id)
        return employee, False
    mismatched_alias = EmployeeIDAlias.objects.filter(
        person_id=person_id, employee__is_active=True
    ).select_related('employee').first()
    if mismatched_alias:
        logger.info(
            "person_id %s is in alias history of '%s' but upload name is '%s' — "
            "treating as reassigned ID; new record will be created",
            person_id, mismatched_alias.employee.name, name
        )

    # Tier 4: Create new
    employee = Employee.objects.create(person_id=person_id, name=name)
    logger.info("Created new employee: %s (person_id=%s)", name, person_id)
    return employee, True


def _parse_remote_daily_csv(file_obj):
    """
    Parse a remote call statistics CSV, handling both old and new report formats.

    Old format: header starts on row 1 (Extension, Answered, ..., Total Ring Duration, Total Talk Duration)
    New format: 4 metadata rows precede the header, columns renamed to Total Ring Time / Total Talk Time

    The header row is located by finding the row containing an 'Extension' column,
    regardless of what other columns (e.g. a leading 'Date' column, used by the monthly
    export) precede or follow it.

    Returns a normalized DataFrame with Total Ring Duration / Total Talk Duration column names.
    """
    raw = file_obj.read()
    if hasattr(file_obj, 'seek'):
        file_obj.seek(0)
    text = raw.decode('utf-8', errors='ignore') if isinstance(raw, bytes) else raw

    lines = text.splitlines()
    header_row_idx = 0
    for i, line in enumerate(lines):
        tokens = [t.strip().strip('"') for t in line.strip().split(',')]
        if 'Extension' in tokens:
            header_row_idx = i
            break

    df = pd.read_csv(io.StringIO(text), skiprows=header_row_idx)
    df = df.rename(columns={
        'Total Ring Time': 'Total Ring Duration',
        'Total Talk Time': 'Total Talk Duration',
    })
    return df


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
@user_passes_test(section_required('upload'), login_url='/report/')
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

        for col in ("First-In", "Last-Out"):
            df[col] = df[col].replace("-", pd.NA)
        date_val = pd.to_datetime(selected_date_str).date()

        df["First-In"] = pd.to_datetime(
            selected_date_str + " " + df["First-In"].astype(str),
            errors="coerce"
        )
        df["Last-Out"] = pd.to_datetime(
            selected_date_str + " " + df["Last-Out"].astype(str),
            errors="coerce"
        )

        # Normalize identifiers BEFORE groupby so type coercion (12345.0 vs "12345"),
        # NBSP, and stray whitespace don't fragment a single employee into multiple
        # groups or cause Tier 0 lookups to miss and silently spawn duplicates.
        df["Person ID"] = df["Person ID"].apply(_clean_id)
        df["Name"] = df["Name"].apply(_clean_name)

        bad_rows = df[df["Person ID"].isna() | df["Name"].isna()]
        if not bad_rows.empty:
            logger.warning(
                "Skipping %d row(s) with missing Person ID or Name in %s",
                len(bad_rows), excel_file.name
            )
        df = df.dropna(subset=["Person ID", "Name"])

        grouped = df.groupby(["Person ID", "Name"], dropna=False)
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
@user_passes_test(section_required('upload'), login_url='/report/')
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
        df = _parse_remote_daily_csv(csv_file)
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
                extension_id = _clean_id(parts[0])
                raw_name = parts[1] if len(parts) > 1 else 'Unknown'
                name = _clean_name(raw_name) or 'Unknown'

                if not extension_id:
                    logger.warning("Skipping remote row with empty extension ID: %r", extension_col)
                    continue

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
@user_passes_test(section_required('upload'), login_url='/report/')
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

        df = _parse_remote_daily_csv(csv_file)

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

                # Parse date: either "MM/DD/YYYY" (new export format, year included)
                # or "Mar. 1" (old format, year appended from the selected month)
                try:
                    if '/' in date_str:
                        record_date = datetime.strptime(date_str, "%m/%d/%Y").date()
                    else:
                        date_text = date_str.replace('.', '').strip()
                        record_date = datetime.strptime(f"{date_text} {selected_year}", "%b %d %Y").date()
                except ValueError:
                    logger.warning("Could not parse date '%s', skipping row", date_str)
                    continue

                dates_processed.add(record_date)

                parts = str(extension_col).split('-', 1)
                extension_id = _clean_id(parts[0])
                raw_name = parts[1] if len(parts) > 1 else 'Unknown'
                name = _clean_name(raw_name) or 'Unknown'

                if not extension_id:
                    logger.warning("Skipping monthly remote row with empty extension ID: %r", extension_col)
                    continue

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


@login_required
@user_passes_test(section_required('upload'), login_url='/report/')
def upload_file_multiday(request):
    """Handle multi-day attendance upload from a Daily Report XLS export."""
    if request.method != 'POST' or not request.FILES.get('multiday_file'):
        return redirect('upload')

    excel_file = request.FILES['multiday_file']

    try:
        _validate_file_extension(excel_file.name, ALLOWED_ATTENDANCE_EXTENSIONS)
    except ValueError as e:
        messages.error(request, str(e))
        return redirect('upload')

    try:
        file_content = excel_file.read()

        if not is_html_excel(file_content):
            messages.error(request, 'Multi-day upload only supports the HTML-based .xls Daily Report format.')
            return redirect('upload')

        if not is_daily_report_excel(file_content):
            messages.error(request, 'Could not find Daily_Report table. Please upload a full daily report export.')
            return redirect('upload')

        df = parse_daily_report_excel(file_content)

        for col in ('First-In', 'Last-Out'):
            df[col] = df[col].replace('-', pd.NA)

        df['Person ID'] = df['Person ID'].apply(_clean_id)
        df['Name'] = df['Name'].apply(_clean_name)

        bad_rows = df[df['Person ID'].isna() | df['Name'].isna()]
        if not bad_rows.empty:
            logger.warning(
                "Skipping %d row(s) with missing Person ID or Name in %s",
                len(bad_rows), excel_file.name
            )
        df = df.dropna(subset=['Person ID', 'Name'])

        # Parse dates; drop rows with unparseable dates
        df['date_val'] = pd.to_datetime(df['Date'].astype(str).str.strip(), errors='coerce').dt.date
        invalid_dates = df['date_val'].isna().sum()
        if invalid_dates:
            logger.warning("Skipping %d row(s) with unparseable dates in %s", invalid_dates, excel_file.name)
        df = df.dropna(subset=['date_val'])

        # Parse check-in/out as full datetimes so groupby min/max works correctly.
        # Biometric exports sometimes have the same employee twice per day with
        # different person_id formats ("8" vs "00000008"). Grouping and taking
        # the earliest check-in / latest check-out merges those duplicate rows.
        df['fi_dt'] = pd.to_datetime(
            df['date_val'].astype(str) + ' ' + df['First-In'].astype(str),
            errors='coerce'
        )
        df['lo_dt'] = pd.to_datetime(
            df['date_val'].astype(str) + ' ' + df['Last-Out'].astype(str),
            errors='coerce'
        )

        processed_count = 0
        new_employees = []
        dates_processed = set()

        grouped = df.groupby(['Person ID', 'Name', 'date_val'], dropna=False)

        with transaction.atomic():
            for (person_id, name, date_val), group in grouped:
                fi_dt = group['fi_dt'].min()
                lo_dt = group['lo_dt'].max()

                fi_time = fi_dt.time() if pd.notna(fi_dt) else None
                lo_time = lo_dt.time() if pd.notna(lo_dt) else None

                employee, created = _lookup_or_create_employee(person_id, name)
                if created:
                    new_employees.append(name)

                fi_time, lo_time = _merge_with_approved_times(employee, date_val, fi_time, lo_time)
                duration = _calculate_work_duration(fi_time, lo_time)

                AttendanceRecord.objects.update_or_create(
                    employee=employee,
                    date=date_val,
                    defaults={
                        'first_in': fi_time,
                        'last_out': lo_time,
                        'work_duration': duration,
                    }
                )
                dates_processed.add(date_val)
                processed_count += 1

        months_processed = {(d.year, d.month) for d in dates_processed}
        for year, month in months_processed:
            call_command('recalculate_summaries', year, month, verbosity=0)

        days_count = len(dates_processed)
        logger.info(
            "Multi-day attendance upload: %d records across %d days by %s",
            processed_count, days_count, request.user.username
        )
        messages.success(
            request,
            f'Multi-day report uploaded! {processed_count} records across {days_count} day(s) processed.'
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
        logger.warning("Multi-day upload validation error: %s", e)
        messages.error(request, f'Error processing file: {e}')
        return redirect('upload')
    except Exception:
        logger.exception("Unexpected error during multi-day attendance upload")
        messages.error(request, 'An unexpected error occurred while processing the file.')
        return redirect('upload')
