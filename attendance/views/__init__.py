"""
Attendance views package.

This package contains all view functions for the attendance application,
organized into logical modules:

- utils.py: Shared utility functions and decorators
- upload.py: File upload views (Excel/CSV)
- reports.py: Attendance report views
- downloads.py: XLSX report download views
- employee_portal.py: Employee self-service portal views
- employee_profile.py: Employee 360° profile view (Phase 3)
- api.py: API endpoints for attendance management
"""

# Import all views for backward compatibility with urls.py
from .utils import superuser_required, parse_duration
from .upload import upload_file, upload_file_multiday, upload_remote_call_stats, upload_remote_monthly
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
    update_remote_attendance,
    recalculate_monthly_summary,
    get_request_attendance_data,
    approve_early_leave,
    decline_early_leave,
    approve_all_on_duty,
    get_pending_count,
    get_pending_requests,
    set_period
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
from .employee_profile import employee_profile  # Phase 3
from .leave_management import (
    leave_management,
    on_duty_requests,
    approve_leave as approve_leave_request,
    reject_leave as reject_leave_request
)
from .annual_leave import (
    annual_leave_management,
    add_annual_leave,
    delete_annual_leave,
)
from .shift_management import (
    special_shift_periods,
    add_special_shift_period,
    update_special_shift_period,
    delete_special_shift_period,
)
from .user_management import (
    user_management,
    create_user,
    update_user,
    delete_user,
)

# Make all views available when importing from attendance.views
__all__ = [
    # Utils
    'superuser_required',
    'parse_duration',
    # Upload
    'upload_file',
    'upload_file_multiday',
    'upload_remote_call_stats',
    'upload_remote_monthly',
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
    'update_remote_attendance',
    'recalculate_monthly_summary',
    'get_request_attendance_data',
    'approve_early_leave',
    'decline_early_leave',
    'approve_all_on_duty',
    'get_pending_count',
    'get_pending_requests',
    'set_period',
    # Employee Management
    'employee_management',
    'update_employee',
    'bulk_update_employees',
    'merge_employees',
    'delete_employee',
    'link_employees',
    'unlink_employees',
    # Employee Profile (Phase 3)
    'employee_profile',
    # Leave Management
    'leave_management',
    'on_duty_requests',
    'approve_leave_request',
    'reject_leave_request',
    # Annual Leave Management
    'annual_leave_management',
    'add_annual_leave',
    'delete_annual_leave',
    # Shift Management
    'special_shift_periods',
    'add_special_shift_period',
    'update_special_shift_period',
    'delete_special_shift_period',
    # User Management
    'user_management',
    'create_user',
    'update_user',
    'delete_user',
]
