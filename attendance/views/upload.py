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
from django.shortcuts import redirect, render

from ..models import (
    AttendanceRecord, EarlyLeaveRequest, Employee,
    RemoteCallRecord, RemoteEmployee,
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
    Look up employee using 3-tier strategy to prevent duplicate creation:
    1. Exact match: person_id + name
    2. Match by name only (if unique)
    3. Create new if no match
    """
    # Tier 1: Exact match on both fields
    employee = Employee.objects.filter(person_id=person_id, name=name).first()
    if employee:
        return employee

    # Tier 2: Match by name
    name_matches = Employee.objects.filter(name=name)
    count = name_matches.count()

    if count == 1:
        employee = name_matches.first()
        if employee.person_id != person_id:
            employee.person_id = person_id
            employee.save(update_fields=['person_id', 'updated_at'])
            logger.info("Updated person_id for employee %s: %s", name, person_id)
        return employee
    elif count == 0:
        # Tier 3: Create new
        employee = Employee.objects.create(person_id=person_id, name=name)
        logger.info("Created new employee: %s (person_id=%s)", name, person_id)
        return employee
    else:
        # Multiple name matches - try to find by person_id among them
        employee = name_matches.filter(person_id=person_id).first()
        if employee:
            return employee
        # Fallback: use the most recently updated
        return name_matches.order_by('-updated_at').first()


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

        for (person_id, name), group in grouped:
            employee = _lookup_or_create_employee(person_id, name)

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

        logger.info(
            "Attendance upload completed: %d employees for %s by %s",
            processed_count, selected_date_str, request.user.username
        )
        messages.success(
            request,
            f'File uploaded and processed successfully! '
            f'{processed_count} employees updated for {selected_date_str}.'
        )
        return redirect('report')

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
        for _, row in df.iterrows():
            extension_col = row.get('Extension', '')

            if str(extension_col).strip().lower() == 'total':
                continue

            if '-' not in str(extension_col):
                continue

            parts = str(extension_col).split('-', 1)
            extension_id = parts[0].strip()
            name = parts[1].strip() if len(parts) > 1 else 'Unknown'

            employee, created = RemoteEmployee.objects.get_or_create(
                extension_id=extension_id,
                name=name
            )
            if created:
                logger.info("Created new remote employee: %s (ext=%s)", name, extension_id)

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

        logger.info(
            "Remote upload completed: %d employees for %s by %s",
            processed_count, selected_date_str, request.user.username
        )
        messages.success(
            request,
            f'Remote call statistics uploaded! Processed {processed_count} employees.'
        )
        return redirect('remote_report')

    except ValueError as e:
        logger.warning("Remote upload validation error: %s", e)
        messages.error(request, f'Error processing remote file: {e}')
        return redirect('upload')
    except Exception:
        logger.exception("Unexpected error during remote upload")
        messages.error(request, 'An unexpected error occurred while processing the file.')
        return redirect('upload')
