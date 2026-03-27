"""
Attendance views package.

This package contains all view functions for the attendance application,
organized into logical modules:

- utils.py: Shared utility functions and decorators
- upload.py: File upload views (Excel/CSV)
- reports.py: Attendance report views
- downloads.py: XLSX report download views
- employee_portal.py: Employee self-service portal views
- api.py: API endpoints for attendance management
"""

# Import all views for backward compatibility with urls.py
from .utils import superuser_required, parse_duration
from .upload import upload_file, upload_remote_call_stats
from .reports import attendance_report, remote_attendance_report
from .downloads import (
    download_report,
    download_employee_report,
    download_remote_report,
    download_remote_employee_report
)
from .employee_portal import (
    employee_login,
    employee_logout,
    employee_portal,
    employee_change_password,
    submit_early_leave_request,
    submit_leave_request,
    get_my_requests
)
from .api import (
    update_attendance,
    recalculate_monthly_summary,
    get_request_attendance_data,
    approve_early_leave,
    decline_early_leave,
    get_pending_count,
    get_pending_requests
)
from .employee_management import (
    employee_management,
    update_employee,
    bulk_update_employees,
    merge_employees,
    delete_employee,
    link_employees,
    unlink_employees,
)
from .leave_management import (
    leave_management,
    approve_leave as approve_leave_request,
    reject_leave as reject_leave_request
)
from .shift_management import (
    special_shift_periods,
    add_special_shift_period,
    update_special_shift_period,
    delete_special_shift_period,
)

# Make all views available when importing from attendance.views
__all__ = [
    # Utils
    'superuser_required',
    'parse_duration',
    # Upload
    'upload_file',
    'upload_remote_call_stats',
    # Reports
    'attendance_report',
    'remote_attendance_report',
    # Downloads
    'download_report',
    'download_employee_report',
    'download_remote_report',
    'download_remote_employee_report',
    # Employee Portal
    'employee_login',
    'employee_logout',
    'employee_portal',
    'employee_change_password',
    'submit_early_leave_request',
    'submit_leave_request',
    'get_my_requests',
    # API
    'update_attendance',
    'recalculate_monthly_summary',
    'get_request_attendance_data',
    'approve_early_leave',
    'decline_early_leave',
    'get_pending_count',
    'get_pending_requests',
    # Employee Management
    'employee_management',
    'update_employee',
    'bulk_update_employees',
    'merge_employees',
    'delete_employee',
    'link_employees',
    'unlink_employees',
    # Leave Management
    'leave_management',
    'approve_leave_request',
    'reject_leave_request',
    # Shift Management
    'special_shift_periods',
    'add_special_shift_period',
    'update_special_shift_period',
    'delete_special_shift_period',
]
