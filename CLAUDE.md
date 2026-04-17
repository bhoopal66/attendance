# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Django-based attendance management system for TCR with dual employee tracking:
- **In-house employees**: Tracked via biometric attendance machines (uploaded as XLS files)
- **Remote employees**: Tracked via phone call statistics (uploaded as CSV files)

The system includes an admin panel for attendance management, an employee portal for viewing attendance and submitting leave requests, and a payroll module.

## Development Commands

```bash
# Activate virtual environment
source venv/bin/activate

# Run development server (SQLite, port 8080)
python manage.py runserver 8080

# Run migrations
python manage.py makemigrations
python manage.py migrate

# Create superuser
python manage.py createsuperuser

# Recalculate monthly summaries
python manage.py recalculate_summaries 2026 3
python manage.py recalculate_summaries 2026 3 --remote
python manage.py recalculate_summaries 2026 3 --employee-id 42

# Production commands (MySQL)
DJANGO_SETTINGS_MODULE=attendance_project.settings.production python manage.py migrate
DJANGO_SETTINGS_MODULE=attendance_project.settings.production python manage.py collectstatic --noinput
DJANGO_SETTINGS_MODULE=attendance_project.settings.production gunicorn --bind 0.0.0.0:8000 attendance_project.wsgi:application
```

No test suite or linting configuration exists yet. Both `attendance/tests.py` and `payroll/tests.py` are empty placeholders.

## Environment Configuration

Copy `.env.example` to `.env` and configure:
- `SECRET_KEY`: Django secret key
- `ALLOWED_HOSTS`: Comma-separated list of allowed hosts
- `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`: MySQL connection (production only)

## Architecture

### Settings (Package)

Settings are organized as a Python package under `attendance_project/settings/`:
- `base.py` - Shared settings (middleware, apps, templates, logging, session config)
- `development.py` - Development overrides (SQLite, DEBUG=True, console logging)
- `production.py` - Production overrides (MySQL, security hardening, WhiteNoise, file logging)
- `__init__.py` - Defaults to development settings

Production uses `DJANGO_SETTINGS_MODULE=attendance_project.settings.production`.

### Apps

- **attendance/** - Main app: attendance tracking, employee management, leave requests, employee portal
- **payroll/** - Payroll dashboard, adjustments, bank submissions, deductions, payslips, and salary setup
- **attendance_project/** - Django project settings and URL routing

### Views (Modular Structure)

Views are split into modules under `attendance/views/`:
- `utils.py` - Shared utilities, constants, decorators, and helper functions
- `upload.py` - XLS/CSV file upload and processing
- `reports.py` - Attendance reports for in-house and remote employees
- `downloads.py` - Excel report generation (openpyxl) with shared styles
- `employee_portal.py` - Employee portal (login, attendance view, leave/early-leave requests)
- `employee_management.py` - Employee CRUD operations with field allowlists
- `leave_management.py` - Leave request approval workflow
- `annual_leave.py` - Annual leave assignment by admin (paid/unpaid, with `AnnualLeave` model)
- `shift_management.py` - Special shift periods (e.g., Ramadan reduced hours) via `SpecialShiftPeriod`
- `api.py` - JSON API endpoints for frontend interactions

All view functions are re-exported from `attendance/views/__init__.py`.

**Key shared utilities in `utils.py`:**
- `MONTH_CHOICES`, `MONTH_NAMES`, `YEAR_RANGE`, `WEEKDAY_HEADERS` - Shared template constants
- `get_selected_month_year()` - Extract/validate month/year from request params
- `build_calendar_grid()` - Build calendar grid data for templates
- `get_holiday_data()` - Get holiday dates/names for a date range
- `count_holidays_in_range()` - Count Sundays and holidays up to a given day
- `get_approved_leave_days()` - Get approved leave day numbers for an employee
- `get_common_report_context()` - Build shared template context for report views
- `superuser_required()` - Single source of truth for the superuser check

### Data Models

**Employee Tracking:**
- `BaseEmployee` (abstract) - Shared fields for both employee types, with date validation
- `Employee` - In-house employees with `person_id` from biometric machines
- `RemoteEmployee` - Remote employees with `extension_id` from phone system
- `AttendanceRecord` - Daily attendance for in-house (unique_together: employee+date)
- `RemoteCallRecord` - Daily call stats for remote (unique_together: employee+date)
- `MonthlySummary` / `RemoteMonthlySummary` - Monthly aggregates
- `ShiftHistory` - Records shift type changes over time for in-house employees
- `EmployeeIDAlias` / `RemoteEmployeeIDAlias` - Alternate IDs for employee deduplication/merging

**Request Management:**
- `EarlyLeaveRequest` - On-duty/field visit requests with clean() validation ensuring exactly one employee FK is set
- `LeaveRequest` - 4 types (sick, medical, annual, casual) with clean() date validation
- `AnnualLeave` - Admin-assigned annual leave blocks (paid/unpaid) spanning date ranges
- `Holiday` - Custom holidays (Sundays are auto-detected)
- `SpecialShiftPeriod` - Date ranges with modified attendance thresholds (e.g., Ramadan)

**Payroll (`payroll/models.py`):**
- `PayrollAdjustment` - Monthly incentives/reductions per employee (both in-house and remote)
- `Bank` - Bank with AED and optional INR per-account charge; `charge_for_currency()` returns the right rate
- `BankSubmission` - Per-employee-per-month submission count per bank; unique per (employee, bank, year, month)
- `DeductionEntry` - Deduction or addition (determined by `entry_type` property); can be split over N months; `installment_amount = total / split_months`; `is_active_in(year, month)` checks if a month falls in the split range
- `DeductionCarryover` - Auto-created when net salary would go negative; `overflow_amount` carries into the following month
- `ExchangeRate` - 1 AED = N units of foreign currency, stored per currency per month
- `GeneratedDocument` - Registry of every payslip/voucher; stable human-readable ref (`PS-XXXXX` / `PV-XXXXX`) via `ref` property

### URL Structure

**Admin Panel:** `/` (upload), `/report/` (in-house), `/report/remote/` (remote), `/employees/`, `/leave-requests/`, `/annual-leave/`, `/special-shifts/`

**Employee Management:** `/employees/update/`, `/employees/bulk-update/`, `/employees/merge/`, `/employees/delete/`, `/employees/link/`, `/employees/unlink/`

**Employee Portal:** `/portal/` (dashboard), `/portal/login/`, `/portal/logout/`, `/portal/change-password/`, `/portal/early-leave-request/`, `/portal/leave-request/`, `/portal/api/my-requests/`

**APIs:** `/api/attendance/update/`, `/api/pending-count/`, `/api/pending-requests/`, `/request/<id>/data/`, `/request/<id>/approve/`, `/request/<id>/decline/`, `/leave/<id>/approve/`, `/leave/<id>/reject/`

**Payroll:** `/payroll/` (dashboard), `/payroll/employees/` (salary setup), `/payroll/banks/`, `/payroll/api/adjustments/`, `/payroll/api/remote-adjustments/`, `/payroll/api/submissions/`, `/payroll/api/deductions/`, `/payroll/api/exchange-rate/save/`, `/payroll/payslip/<emp_type>/<id>/`, `/payroll/voucher/advance/`

### Authentication

**Admin Users:** Standard Django authentication (`@login_required`, `@user_passes_test(superuser_required)`). Login at `/login/`, redirects to `/report/`.

**Employee Portal:** Custom authentication using `portal_password` field (hashed with `make_password`). Session-based with `employee_id` stored in session. No Django User objects created.

### Frontend

Server-rendered templates with AJAX for admin approval workflows. No frontend build step.

Key patterns:
- Purple-themed UI (`--primary-color: #4F46E5`) with CSS variables in `base.css`
- Calendar grid components shared between admin and portal views
- AJAX form submissions with CSRF token headers for approval/decline actions
- Real-time polling for pending requests (30s interval)
- CSS versioning in templates via `?v=X` query params for cache busting

### Logging

Structured logging via Python `logging` module, configured in `settings/base.py`:
- Two loggers: `attendance` and `payroll`
- Development: console output only
- Production: console + rotating file (`logs/attendance.log`, 5MB max, 5 backups)
- All views use `logger = logging.getLogger('attendance')` for structured logging
- Key operations logged: uploads, login/logout, approval actions, attendance edits

### Error Pages

Custom error templates at `attendance/templates/`: `400.html`, `403.html`, `404.html`, `500.html`

### Other Components

- `attendance/context_processors.py` - `pending_requests_processor` makes pending on-duty request counts available to all templates for authenticated superusers
- `attendance/templatetags/attendance_extras.py` - Custom template filters
- `attendance/management/commands/recalculate_summaries.py` - Management command to rebuild monthly summaries

## Critical Implementation Details

### Employee Lookup Strategy (XLS Upload)

When processing XLS uploads (`upload.py:_lookup_or_create_employee`), the system uses a **3-tier lookup strategy** to prevent duplicate employee creation:
1. Try exact match: `person_id` + `name`
2. If multiple matches: Use most recently updated employee
3. If no match: Create new employee record

This is critical when the same `person_id` is reused for different employees or when names are duplicated.

### Attendance Status Calculation

**In-house employees:**
- Green (Present): On time with full day
- Yellow (Late/Early): Late arrival (before 12:00) OR early departure
- Orange (Half Day): Arrival after 12:00
- Red (Absent): No attendance record + not holiday/Sunday
- Blue (Paid Leave): Approved leave request
- Purple (Holiday): Sunday or custom holiday

**Remote employees:**
Status auto-calculated on save via `calculate_attendance_status()` based on talk duration and weekday:
- Mon-Thu: <45min=Absent, 45-89min=Half, >=90min=Present
- Friday: <30min=Absent, 30-59min=Half, >=60min=Present
- Saturday: <=20min=Absent, 21-44min=Half, >=45min=Present

### Early Leave Request Workflow

1. Employee submits with destination, customer name, times
2. Admin reviews existing attendance data in modal
3. Admin approves with optional `approved_first_in`/`approved_last_out` times
4. These times are merged with biometric data during next XLS upload

### Leave Request Workflow

1. Employee submits request via portal (document upload required for sick/medical)
2. Admin reviews and can approve with custom `approved_days` (can be less than requested)
3. Approved leaves appear in attendance calendar as Paid Leave (blue)

## Static Files

- Development: Served from `attendance/static/`
- Production: WhiteNoise middleware + `collectstatic` to `staticfiles/`

## Payroll Architecture

All payroll logic lives in `payroll/views.py`. Every model (payroll and attendance) uses the dual-FK pattern: `employee` (in-house) and `remote_employee` (remote) — exactly one must be set; `clean()` enforces this.

### Employee Fields That Drive Payroll

These fields on `Employee` / `RemoteEmployee` control how payroll is calculated:

| Field | Values | Effect |
|-------|--------|--------|
| `department` | `'Admin'`, `'Sales'`, `None` | Determines which dashboard section the employee appears in |
| `salary` | Decimal or null | Monthly gross salary |
| `currency` | `'AED'` (default), `'INR'` | Governs commission calculation path and payslip formatting |
| `payroll_type` | `'attendance'`, `'performance'` | Admin section only: `'performance'` skips all late/absent deductions |
| `is_fixed_salary` | bool | Sales section only: attendance-based salary instead of pure commission |
| `tcr_id` | string | Cross-links in-house and remote records for the same person; remote employees whose `tcr_id` matches an active in-house employee are excluded from the Sales section to prevent double-counting |
| `location` | string | On `RemoteEmployee`, value `'inhouse'` moves the employee to the in-house column of the Salary Setup page (`/payroll/employees/`) |

### Dashboard Sections (`_get_inhouse_payroll_row` / `_get_sales_payroll_row`)

**Admin section** — in-house employees with `department='Admin'`:
- Salary split: Basic 40%, Housing 40%, Transport 20%
- `daily_rate = salary / days_in_month`
- Deductions: `(absent_days + half_days×0.5 + (late_days÷3)×0.5) × daily_rate`
- `payroll_type='performance'` → zero deductions regardless of attendance

**Sales section** — in-house `department='Sales'` + remote employees (excluding those with matching in-house `tcr_id`):
- Default: pure commission (submission_count × bank rate), no base salary
- `is_fixed_salary=True`: attendance-based salary computed on-the-fly from raw records; bank counts are still recorded but ignored for net payroll
- `is_fixed_salary=False` + salary set + `payroll_type='attendance'`: salary scaled by attendance ratio, commission added on top

### DSA Commission Calculation (`_get_commission`)

- **AED employees**: `commission = Σ (submission_count × bank.per_account_charge)`
- **INR employees**: tiered via `_calc_inr_tiered_commission()`:
  - First `INR_COMMISSION_THRESHOLD` (4) accounts across all banks: bank's `inr_per_account_charge` each
  - Every account beyond threshold: `INR_OVERFLOW_RATE` (3000 INR) each
  - Banks are processed alphabetically; the 4-account cap is a rolling total, not per-bank

### Annual Leave Compensation (`_annual_leave_day_counts`)

`AnnualLeave` has `is_paid` and `salary_percentage` (0–100%). During payroll:
- **Working days** (Mon–Sat, non-holiday): compensate `salary_pct%` of `daily_rate × working_days` — this offsets the absent-day deduction already applied via `base_payroll`
- **Non-working days** (Sundays + holidays): normally paid at 100%; during leave, only paid at `salary_pct%` — deduct the remaining `(100 − salary_pct)%`

### Deduction Entry Mechanics

`DeductionEntry.split_months > 1` spreads the total over N consecutive months starting from `start_year`/`start_month`. Use `is_active_in(year, month)` to test applicability. When net salary goes negative, `DeductionCarryover` records the overflow to apply it the following month.

### Payslips and Vouchers

- `download_payslip(emp_type, emp_id)` — generates an in-memory PDF-style Excel payslip; registers a `GeneratedDocument` entry
- `advance_voucher_download()` — generates an advance payment voucher for a `DeductionEntry` of type `advance`
- Both documents get stable reference IDs via `GeneratedDocument.ref` (`PS-XXXXX` / `PV-XXXXX`)

### Remote Attendance Thresholds in Payroll

Sales payroll recomputes remote attendance inline (does not rely on stored `RemoteCallRecord.attendance_status`) using `get_active_special_periods_for_month()` and `get_remote_thresholds_from_period()` from `attendance/views/utils.py`. This ensures correctness if `is_fixed_salary` was toggled after records were saved.

## Git Workflow

Main branch: `main`
Remote: `git@github.com:yadhumanikandan/attendance_system.git`

## Production Deployment

Deployed on Ubuntu 24.04 with Gunicorn, MySQL, systemd service (`attendance.service`), and WhiteNoise. See `DEPLOYMENT.md` for complete setup guide.

Update process:
```bash
cd /var/www/attendance
source venv/bin/activate
git pull origin main
DJANGO_SETTINGS_MODULE=attendance_project.settings.production python manage.py migrate
DJANGO_SETTINGS_MODULE=attendance_project.settings.production python manage.py collectstatic --noinput
sudo systemctl restart attendance
```

**Note:** The deployment `attendance.service` file needs `DJANGO_SETTINGS_MODULE` updated from `attendance_project.settings_production` to `attendance_project.settings.production`.
