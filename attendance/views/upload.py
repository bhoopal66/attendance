"""
Upload views for attendance data.
Handles Excel uploads for in-house employees and CSV uploads for remote employees.
"""

import pandas as pd
import os
import re
from datetime import timedelta, datetime
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from bs4 import BeautifulSoup

from ..models import Employee, AttendanceRecord, RemoteEmployee, RemoteCallRecord, EarlyLeaveRequest
from .utils import superuser_required, parse_duration


def parse_html_excel(file_content):
    """
    Parse HTML-based Excel file (.xls exported from web systems).
    Returns a tuple of (dataframe, extracted_date).
    """
    # Decode content if bytes
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
    
    # Get all td elements
    tds = punch_table.find_all('td')
    
    # Each row has 11 columns: Index, Person ID, Name, Department, Position, Gender, Date, Day Of Week, Timetable, First-In, Last-Out
    rows = []
    for i in range(0, len(tds), 11):
        if i + 11 <= len(tds):
            row = [td.get_text(strip=True) for td in tds[i:i+11]]
            rows.append(row)
    
    if not rows:
        raise ValueError("No attendance data found in the file")
    
    columns = ['Index', 'Person ID', 'Name', 'Department', 'Position', 'Gender', 'Date', 'Day Of Week', 'Timetable', 'First-In', 'Last-Out']
    df = pd.DataFrame(rows, columns=columns)
    
    return df, extracted_date


def is_html_excel(file_content):
    """Check if the file content is HTML-based Excel."""
    try:
        # Check for HTML signature at the beginning
        if isinstance(file_content, bytes):
            content_start = file_content[:500].decode('utf-8', errors='ignore').lower()
        else:
            content_start = file_content[:500].lower()
        return '<html' in content_start or '<!doctype html' in content_start
    except:
        return False


@login_required
@user_passes_test(superuser_required, login_url='/report/')
def upload_file(request):
    """Handle Excel file upload for in-house attendance data."""
    if request.method == 'POST' and request.FILES.get('file'):
        excel_file = request.FILES['file']
        selected_date_str = request.POST.get('date')  # Optional now - can be extracted from file
        
        filename = excel_file.name
        _, ext = os.path.splitext(filename)
        ext = ext.lower()
        
        try:
            # Read file content
            file_content = excel_file.read()
            excel_file.seek(0)  # Reset file pointer
            
            # Check if it's an HTML-based Excel file
            if is_html_excel(file_content):
                # Parse HTML-based .xls file
                df, extracted_date = parse_html_excel(file_content)
                
                # Use extracted date if no date was selected
                if not selected_date_str and extracted_date:
                    selected_date_str = extracted_date
                elif not selected_date_str:
                    messages.error(request, 'Could not extract date from file. Please select a date manually.')
                    return redirect('upload')
                    
            else:
                # Standard Excel file processing
                if not selected_date_str:
                    messages.error(request, 'Please select a date.')
                    return redirect('upload')
                
                engine = "xlrd" if ext == ".xls" else "openpyxl"
                df = pd.read_excel(excel_file, engine=engine)

            # Replace '-' with NaN
            df.replace("-", pd.NA, inplace=True)

            # Parse date
            date_val = pd.to_datetime(selected_date_str).date()

            # Combine Selected Date + Time columns
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
                # Look up employee - try multiple strategies:
                # 1. Exact match on person_id + name
                # 2. Match by name only (if unique)  
                # 3. Create new if no match
                employee = None
                
                # First try exact match on both fields
                employee = Employee.objects.filter(person_id=person_id, name=name).first()
                
                if not employee:
                    # Try matching by name only if unique
                    name_matches = Employee.objects.filter(name=name)
                    if name_matches.count() == 1:
                        employee = name_matches.first()
                        # Update person_id if it changed
                        if employee.person_id != person_id:
                            employee.person_id = person_id
                            employee.save()
                    elif name_matches.count() == 0:
                        # No match - create new employee
                        employee = Employee.objects.create(
                            person_id=person_id,
                            name=name
                        )
                    else:
                        # Multiple matches by name - try to find by person_id among them
                        employee = name_matches.filter(person_id=person_id).first()
                        if not employee:
                            # Use the first one with matching name
                            employee = name_matches.first()

                first_in = group["First-In"].min()
                last_out = group["Last-Out"].max()
                
                # Preserve partial times - don't lose check-in if checkout is missing or vice versa
                fi_time = first_in.time() if not pd.isna(first_in) else None
                lo_time = last_out.time() if not pd.isna(last_out) else None
                
                # Check for approved on-duty requests for this employee on this date
                # If found, merge times: take earliest check-in and latest checkout
                approved_request = EarlyLeaveRequest.objects.filter(
                    employee=employee,
                    request_date=date_val,
                    status='approved'
                ).first()
                
                if approved_request:
                    # Merge with approved times
                    if approved_request.approved_first_in:
                        if fi_time:
                            # Take the earlier of the two check-in times
                            fi_time = min(fi_time, approved_request.approved_first_in)
                        else:
                            fi_time = approved_request.approved_first_in
                    
                    if approved_request.approved_last_out:
                        if lo_time:
                            # Take the later of the two checkout times
                            lo_time = max(lo_time, approved_request.approved_last_out)
                        else:
                            lo_time = approved_request.approved_last_out
                
                # Calculate duration only if both times exist
                if fi_time and lo_time:
                    # Calculate duration in seconds then convert to timedelta
                    duration_seconds = (
                        lo_time.hour * 3600 + lo_time.minute * 60 + lo_time.second
                    ) - (
                        fi_time.hour * 3600 + fi_time.minute * 60 + fi_time.second
                    )
                    duration = timedelta(seconds=max(0, duration_seconds))
                else:
                    duration = timedelta(0)

                # Create or Update AttendanceRecord
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

            messages.success(request, f'File uploaded and processed successfully! {processed_count} employees updated for {selected_date_str}.')
            return redirect('report')

        except Exception as e:
            messages.error(request, f'Error processing file: {str(e)}')
            return redirect('upload')

    return render(request, 'attendance/upload.html')


@login_required
@user_passes_test(superuser_required, login_url='/report/')
def upload_remote_call_stats(request):
    """Handle CSV upload for remote team call statistics."""
    if request.method == 'POST' and request.FILES.get('remote_file'):
        csv_file = request.FILES['remote_file']
        selected_date_str = request.POST.get('remote_date')
        
        if not selected_date_str:
            messages.error(request, 'Please select a date for remote call statistics.')
            return redirect('upload')
        
        try:
            # Read CSV file
            df = pd.read_csv(csv_file)
            
            # Parse the selected date
            selected_date = pd.to_datetime(selected_date_str).date()
            
            processed_count = 0
            for _, row in df.iterrows():
                extension_col = row.get('Extension', '')
                
                # Skip Total row
                if str(extension_col).strip().lower() == 'total':
                    continue
                
                # Parse extension: "3068-Maria"
                if '-' in str(extension_col):
                    parts = str(extension_col).split('-', 1)
                    extension_id = parts[0].strip()
                    name = parts[1].strip() if len(parts) > 1 else 'Unknown'
                else:
                    continue  # Skip invalid rows
                
                # Get or create remote employee
                employee, created = RemoteEmployee.objects.get_or_create(
                    extension_id=extension_id,
                    name=name
                )
                
                # Parse call statistics
                answered = int(row.get('Answered', 0) or 0)
                no_answered = int(row.get('No Answered', 0) or 0)
                busy = int(row.get('Busy', 0) or 0)
                failed = int(row.get('Failed', 0) or 0)
                voicemail = int(row.get('Voicemail', 0) or 0)
                
                # Parse durations
                ring_duration = parse_duration(row.get('Total Ring Duration', ''))
                talk_duration = parse_duration(row.get('Total Talk Duration', ''))
                
                # Create or update call record (attendance status calculated in save())
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
            
            messages.success(request, f'Remote call statistics uploaded! Processed {processed_count} employees.')
            return redirect('remote_report')
            
        except Exception as e:
            messages.error(request, f'Error processing remote file: {str(e)}')
            return redirect('upload')
    
    return redirect('upload')
