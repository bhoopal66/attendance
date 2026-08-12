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

# Re-evaluate attendance status for fixed-salary remote employees (dry-run by default)
python manage.py fix_remote_attendance
python manage.py fix_remote_attendance --apply

# Production commands (MySQL)
DJANGO_SETTINGS_MODULE=attendance_project.settings.production python manage.py migrate
DJANGO_SETTINGS_MODULE=attendance_project.settings.production python manage.py collectstatic --noinput
DJANGO_SETTINGS_MODULE=attendance_project.settings.production gunicorn --bind 0.0.0.0:8000 attendance_project.wsgi:application
```

## Production Server

This is a **production environment** running on Gunicorn managed by systemd. Do **not** use `manage.py runserver`.

```bash
# Restart the server (required after code changes)
sudo systemctl restart attendance

# Check server status
sudo systemctl status attendance

# View live logs
sudo journalctl -u attendance -f
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
- `reports.py` - Attendance reports for in-house and remote employees; remote report also computes `team_summary` (total_employees, total_present, total_half, total_absent) for the KPI bar at the top of the page
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
- `require_post_json` - Decorator that enforces POST + JSON content-type on API views

**Bulk query helpers** (avoid N+1 in report views that iterate many employees):
- `get_bulk_employee_shifts(employees, date)` → `{employee_id: (shift_start, shift_end)}` — same 3-tier priority as `get_employee_shift_for_date` but in a single DB query
- `get_bulk_approved_leave_days(employees, start, end)` → `{employee_id: set_of_day_ints}`
- `get_bulk_annual_leave_non_working_days(employees, start, end, holiday_dates)` → `{employee_id: int}`

Use these instead of the single-employee variants whenever rendering a full month's report for all employees.

### Data Models

**Employee Tracking:**
- `BaseEmployee` (abstract) - Shared fields for both employee types, with date validation; includes `visa_provider` (choices: Jumbo, OnTime, Taamul; nullable — blank = own-visa employee) and `salary_cycle_start_day` (default 21; see [Salary Cycle](#salary-cycle-pay-period))
- `Employee` - In-house employees with `person_id` from biometric machines
- `RemoteEmployee` - Remote employees with `extension_id` from phone system
- `AttendanceRecord` - Daily attendance for in-house (unique_together: employee+date)
- `RemoteCallRecord` - Daily call stats for remote (unique_together: employee+date)
- `MonthlySummary` / `RemoteMonthlySummary` - Monthly aggregates
- `ShiftHistory` - Records shift type changes over time for in-house employees
- `EmployeeIDAlias` / `RemoteEmployeeIDAlias` - Alternate IDs for employee deduplication/merging

**Request Management:**
- `EarlyLeaveRequest` - On-duty/field visit requests with clean() validation ensuring exactly one employee FK is set; managed via the dedicated `/on-duty-requests/` admin page (`attendance/views/leave_management.py:on_duty_requests`), separate from the `LeaveRequest` workflow below
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
- `ExchangeRate` - 1 AED = N units of foreign currency, stored per currency per month; to convert foreign → AED: `amount / rate`
- `GeneratedDocument` - Registry of every payslip/voucher; stable human-readable ref (`PS-XXXXX` / `PV-XXXXX`) via `ref` property
- `FrozenPayrollMonth` - Immutable JSON snapshot of a fully-computed payroll month; once frozen, dashboard serves from this snapshot instead of recalculating — freeze/unfreeze via `/payroll/api/freeze/` and `/payroll/api/unfreeze/`
- `PaidSalaryRecord` - Per-employee immutable payroll snapshot created when salary is marked as paid; stores full snapshot (attendance, deductions, commission, bank submissions, final salary) at the moment of payment — mark/unmark via `/payroll/api/mark-paid/` and `/payroll/api/unmark-paid/`

### URL Structure

**Admin Panel:** `/` (upload), `/upload/multiday/` (multi-day Daily Report upload), `/report/` (in-house), `/report/remote/` (remote), `/employees/`, `/on-duty-requests/` (early-leave/on-duty request queue), `/leave-requests/`, `/annual-leave/`, `/special-shifts/`

**Employee Management:** `/employees/update/`, `/employees/bulk-update/`, `/employees/merge/`, `/employees/delete/`, `/employees/link/`, `/employees/unlink/`

**Employee Portal:** `/portal/` (dashboard), `/portal/login/`, `/portal/logout/`, `/portal/change-password/`, `/portal/early-leave-request/`, `/portal/leave-request/`, `/portal/api/my-requests/`

**APIs:** `/api/attendance/update/`, `/api/remote/attendance/update/`, `/api/pending-count/`, `/api/pending-requests/`, `/request/<id>/data/`, `/request/<id>/approve/`, `/request/<id>/decline/`, `/on-duty-requests/approve-all/`, `/leave/<id>/approve/`, `/leave/<id>/reject/`

**Payroll:** `/payroll/` (comprehensive dashboard, `payroll_test_dashboard` — see below), `/payroll/old/` (legacy dashboard, `payroll_dashboard`), `/payroll/employees/` (salary setup), `/payroll/banks/`, `/payroll/api/adjustments/`, `/payroll/api/remote-adjustments/`, `/payroll/api/submissions/<emp_type>/<id>/`, `/payroll/api/submissions/save/`, `/payroll/api/upload-submissions/` (bulk XLSX), `/payroll/api/deductions/add/`, `/payroll/api/deductions/autofill/`, `/payroll/api/recalculate/`, `/payroll/api/exchange-rate/save/`, `/payroll/api/freeze/`, `/payroll/api/unfreeze/`, `/payroll/api/mark-paid/`, `/payroll/api/unmark-paid/`, `/payroll/api/employee/<emp_type>/<id>/update/`, `/payroll/payslip/<emp_type>/<id>/`, `/payroll/voucher/advance/`

Note: `/payroll/` and `/payroll/test/` both route to `payroll_test_dashboard` — the "test" dashboard is now the primary one. The original `payroll_dashboard` view lives on at `/payroll/old/` for reference/rollback.

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
- `attendance/templatetags/attendance_extras.py` - Custom template filters: `is_in_list`, `dictsumby` (sums a key across a list of dicts or objects)
- `attendance/management/commands/recalculate_summaries.py` - Management command to rebuild monthly summaries
- `payroll/management/commands/convert_frozen_to_paid.py` - One-time migration converting `FrozenPayrollMonth` snapshots into per-employee `PaidSalaryRecord` entries, for the new dashboard's paid-record overlay; supports `--dry-run`

### XLS Upload Format

The biometric machine exports `.xls` files that are actually HTML documents (not binary Excel). `upload.py` detects this automatically via `is_html_excel()` (checks for `<html` in the first 500 bytes) and parses them with BeautifulSoup (`parse_html_excel()`), looking for a `<table class="Punch_Report">` with 11 columns per row. Standard `.xls`/`.xlsx` files are handled with pandas/xlrd. Both paths normalise into the same DataFrame before processing. If a file parses unexpectedly, check which path was taken.

There is also a separate **multi-day** upload path (`upload_file_multiday`, `/upload/multiday/`) for a "Daily Report" HTML export covering many days at once. It's detected via `is_daily_report_excel()` (looks for `Daily_Report` in the content) and parsed by `parse_daily_report_excel()`, which reads a 20-column-per-row `<table class="Daily_Report">` (columns include an embedded `Date` per row, unlike the single-day `Punch_Report` format) and reduces it to `Person ID`/`Name`/`Date`/`First-In`/`Last-Out`.

Employee/person IDs are normalized by `_clean_id()`, which also strips leading zeros from purely numeric IDs (`"00000008"` → `"8"`) so both formats exported by the biometric machine match the same stored employee.

Remote call-stat CSV uploads (`upload_remote_call_stats`) go through `_parse_remote_daily_csv()`, which transparently handles two report formats: an old format with the header on row 1, and a newer format with 4 metadata rows before the header and renamed `Total Ring Time`/`Total Talk Time` columns (normalized back to `Total Ring Duration`/`Total Talk Duration`).

### Employee Lookup Strategy (XLS Upload)

When processing XLS uploads (`upload.py:_lookup_or_create_employee`), the system uses a tiered strategy to prevent duplicate employee creation. Confirmed employees (those with `tcr_id` set) take priority at every tier:

- **Tier 0**: Exact match on `person_id` + `name` (fast path)
- **Tier 1a**: Confirmed employee (`tcr_id` set) matched by name — updates `person_id` if it changed, archives old ID as an alias
- **Tier 1b**: Confirmed employee matched by `person_id` + `name`; if `person_id` matches but name differs, treats it as a reassigned machine slot (creates a new record)
- **Tier 2**: Active employee without `tcr_id` matched by name — updates `person_id` and archives old ID; if multiple match, uses most recently updated
- **Tier 3**: Check `EmployeeIDAlias` history for active employees (name must also match, otherwise treats as reassigned ID)
- **Tier 4**: Create new employee record

This is critical when the same `person_id` is reused (leavers' machine slots reassigned) or employee names have changed.

### Attendance Status Calculation

**In-house employees:**
- Green (Present): On time with full day
- Yellow (Late/Early): Late arrival (before 12:00) OR early departure
- Orange (Half Day): Arrival after 12:00
- Red (Absent): No attendance record + not holiday/Sunday
- Blue (Paid Leave): Approved leave request, or `AttendanceRecord.is_paid_leave=True` set directly on the record (not deducted from salary)
- Purple (Holiday): Sunday or custom holiday
- **WFH override**: `AttendanceRecord.is_work_from_home=True` always counts as full-day Present, regardless of punch times
- **Fixed salary override**: `Employee.is_fixed_salary=True` means punch-in alone counts as Present (no punch-out or duration threshold required)

**Remote employees:**
Status auto-calculated on save via `calculate_attendance_status()` based on talk duration and weekday:
- Mon-Thu: <45min=Absent, 45-89min=Half, >=90min=Present
- Friday: <30min=Absent, 30-59min=Half, >=60min=Present
- Saturday: <=20min=Absent, 21-44min=Half, >=45min=Present
- **Fixed salary override**: `RemoteEmployee.is_fixed_salary=True` means any call activity (answered, no-answered, busy, or failed) counts as Present regardless of talk duration

Default thresholds can be overridden per period via `SpecialShiftPeriod` remote threshold fields.

### Bridge Sunday Rule

A Sunday that falls between an approved-leave Saturday and an approved-leave Monday is treated as an **unpaid absent day** in payroll (one `daily_rate` deducted), even though it is normally a holiday. This bridges the gap so employees cannot claim a free Sunday by surrounding it with leave days.

Logic lives in `attendance/views/utils.py:get_bridge_sunday_days()`. It checks both `LeaveRequest` (in-house only) and `AnnualLeave` (in-house and remote). The two sources are handled differently:
- **`AnnualLeave` spans**: any Sunday that falls anywhere inside the span is excluded from the bridge count (not just Sundays flanked by leave on both sides) because the payroll calculation's `annual_leave_extra_deduction` already charges `(100 − salary_pct)%` for non-working days within the span — counting them again here would be a double deduction.
- **`LeaveRequest` spans**: Sundays within the span are **not** excluded and are still counted as bridge Sundays, because `LeaveRequest` records are not charged by any other mechanism.

### Salary Cycle / Pay Period

Each employee has a `salary_cycle_start_day` (default `21`). This controls the date range used for attendance calculation in payroll:

- **`cycle_start_day = 21`** (default): period is the 21st of the previous month to the 20th of the current month. This is a cross-calendar-month period, so `MonthlySummary` (which is calendar-month only) cannot be used. Attendance is computed directly from `AttendanceRecord` rows for the period.
- **`cycle_start_day = 1`**: period is the 1st to last day of the current month (standard calendar month). `MonthlySummary` is used when available.

Helper: `_get_employee_pay_period(cycle_start_day, year, month)` in `payroll/views.py` returns `(period_start, period_end, days_in_period, total_holidays)`. Both `_get_inhouse_payroll_row` and `_get_sales_payroll_row` accept `period_start`/`period_end` to support custom cycles.

### Remote Attendance Inline Editing

Admins can edit remote call records directly from the remote attendance report. The new API endpoint `POST /api/remote/attendance/update/` (`update_remote_attendance` in `api.py`) accepts `employee_id`, `date`, `talk_minutes`, and `answered_calls`. It upserts a `RemoteCallRecord` and returns the recalculated `attendance_status`. The remote report template renders an edit icon on each day cell and a modal form wired to this endpoint.

### Payroll Dashboard (`/payroll/`, view `payroll_test_dashboard`, template `payroll/test_dashboard.html`)

This is the primary payroll dashboard (both `/payroll/` and `/payroll/test/` route here); it replaced the original dashboard workflow, which is still reachable at `/payroll/old/` (view `payroll_dashboard`) for reference/rollback. Key differences from the old dashboard:

- **Deductions table**: All `DeductionEntry` records rendered as a matrix of categories (advance, visa_status_change, clawback, leave_deduction, late_deduction, other_deduction, last_month_balance, paid_leave, other_addition) per employee. `leave_deduction` and `late_deduction` are auto-computed for attendance-based employees.
- **Final summary**: Combines payroll net + deductions + additions → `final_salary` per employee; automatically creates `DeductionCarryover` when `final_salary < 0`.
- **Carryover schedule**: Shows all `DeductionCarryover` records with statuses (pending / partial / cleared) and highlights which are incoming/outgoing for the selected month.
- **Per-employee pay periods**: Each employee's `salary_cycle_start_day` is respected; the `_emp_period(emp)` closure inside the view resolves the correct date range for every row.

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
| `visa_provider` | `'Jumbo'`, `'OnTime'`, `'Taamul'`, `None` | Manpower visa provider; null = own-visa employee |
| `salary_cycle_start_day` | int, default `21` | Pay period start day — `21` means 21st of previous month to 20th of current; `1` means calendar month (1st to last day) |

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

### Payroll Freeze / Unfreeze

Once a month's payroll is finalised, it can be frozen via `POST /payroll/api/freeze/`. This serialises the entire computed context into `FrozenPayrollMonth.snapshot` (JSONField). The dashboard then serves from that snapshot for that month — live employee/bank/attendance changes no longer affect the displayed figures. Unfreezing (`POST /payroll/api/unfreeze/`) deletes the snapshot and reverts to live recalculation. The payslip download still works against live data even when a month is frozen.

### Mark as Paid / Unmark

`POST /payroll/api/mark-paid/` accepts `{year, month, employees: [{id, type}]}`. It re-computes the full payroll row for each specified employee and writes an immutable `PaidSalaryRecord` (using `update_or_create`). The snapshot stores all line items at the moment of payment — attendance, deductions breakdown, commission, bank submissions, final salary — and is never recalculated. `POST /payroll/api/unmark-paid/` deletes the corresponding records.

**Difference from `FrozenPayrollMonth`**: Freeze is a whole-month dashboard lock; Mark-as-Paid is per-employee and persists the computed row independently of the freeze state.

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

