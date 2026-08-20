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

# Payroll regression harness: snapshot a month's computed payroll to detect unintended drift
python manage.py payroll_snapshot --year 2026 --month 7 --write   # save baseline (payroll/regression_baselines/)
python manage.py payroll_snapshot --year 2026 --month 7 --check   # compare current calc against baseline

# Explain one employee's payroll month line-by-line, or why leave/attendance reduced their salary
python manage.py explain_payroll_row --tcr TCR1000224 --year 2026 --month 7
python manage.py diagnose_paid_leave --tcr TCR1000224 --year 2026 --month 7

# One-time backfills/seeds for the assignments/approvals/identity/leave-policy engine (dry-run by default, --apply to write)
python manage.py backfill_leave_ledger --apply
python manage.py backfill_timeline --apply
python manage.py backfill_assignments --apply
python manage.py backfill_visas --apply
python manage.py seed_company
python manage.py seed_leave_policy
python manage.py seed_approval_chains

# Production commands (MySQL)
DJANGO_SETTINGS_MODULE=attendance_project.settings.production python manage.py migrate
DJANGO_SETTINGS_MODULE=attendance_project.settings.production python manage.py collectstatic --noinput
DJANGO_SETTINGS_MODULE=attendance_project.settings.production gunicorn --bind 0.0.0.0:8000 attendance_project.wsgi:application
```

## Production Server

This is a **production environment** running on Gunicorn managed by systemd. Do **not** use `manage.py runserver`.

> **⚠️ DO NOT TOUCH PORT 8000.** This working directory (`/home/ubuntu/attendance` / `attendance-staging.service`, port 8001, DB `attendance_db_staging`) is a separate deployment from the main production app at `/var/www/attendance` (`attendance.service`, port 8000, DB `attendance_db`). **Never** run `git pull`, `manage.py migrate`/`makemigrations`, `collectstatic`, or `systemctl restart/stop` against port 8000/`attendance.service`/`/var/www/attendance`, and never touch its database directly. This applies even to generic requests like "update the app," "redeploy," or "make migrations and migrate" — always assume those mean the **port 8001** instance in this directory unless the user explicitly names port 8000 / `/var/www/attendance` / `attendance.service` and confirms they want that instance touched.

```bash
# Restart the server (required after code changes) — PORT 8001 STAGING ONLY
sudo systemctl restart attendance-staging

# Check server status
sudo systemctl status attendance-staging

# View live logs
sudo journalctl -u attendance-staging -f
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
- `employee_profile.py` - Employee 360° profile (`/employees/<person_id>/profile/`): onboarding checklist, employment/salary/employer-cost history, documents, recoverables, and a 12-month performance trend. Also owns `compliance_reveal` (the audited "unmask one field" endpoint). See [Employee 360° Profile](#employee-360-profile)
- `compliance.py` - Read-only Compliance Watchlist (`/compliance/`, `/compliance/export/`). See [Compliance Watchlist](#compliance-watchlist)
- `user_management.py` - Django auth `User` account management for IT admins (`/user-management/...`), gated by `it_admin_required` rather than `superuser_required`. See [User Management](#user-management)

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

**Employee 360° Profile (`attendance/models.py`):**
- `EmploymentHistory` - Auto-diffed log of employment-field changes (designation, department, team, location, employment_status, reporting_manager), written by `employee_profile.py:_save_employment`
- `SalaryStructure` - Approved salary revisions over time; saving a new one supersedes the prior approved row and syncs `Employee.salary`/`currency`
- `EmployerCostSetup` - Employer-side cost history per employee (used by payroll profitability calculations, see `services_profitability.py`)
- `EmployeeDocument` - Uploaded documents (e.g. visa, ID) per employee
- `Recoverable` - Amounts owed by an employee to the company; can be linked to a `DeductionEntry` for recovery

**Audit & Access Control (`attendance/models.py`):**
- `AuditLog` - Append-only audit trail for financial/role-changing actions. Deliberately decoupled from FKs (`app_label`/`model_name`/`object_id`/`object_repr` as plain fields, not `GenericForeignKey`) so it survives deletion of the audited row. `changes` is a JSONField of `{field: [old, new]}`. See [Audit Logging](#audit-logging)
- `UserProfile` - One-to-one with Django `User`; holds `is_it_admin`, per-user nav-section permissions (`allowed_sections`, `sections_restricted` against `NAV_SECTIONS`/`NAV_SECTION_KEYS` in `attendance/views/utils.py`), and `role` (`hr_admin`/`exec_director`/`manager`/`it`, default none). `role` is a **second, orthogonal** permission axis from `allowed_sections`: sections gate which *pages* a user may open; `role` (checked via `attendance/compliance_access.py`) gates which *compliance fields* (Emirates ID, IBAN, salary, etc.) they may see once there. A user can have the Employees section but no role, and will see zero identity/bank/commission data.

**Org/HR Master Data & Multi-Entity (`attendance/models.py`):**
- `Department`, `Team` (FK→`Department`), `Location`, `DesignationMaster` (FK→`Department`) — simple master lists intended to eventually replace the CharField choices on `BaseEmployee` (`department`, etc.); today they exist as standalone lookup tables managed only via Django Admin and are **not yet FKs from `Employee`/`EmployeeAssignment`** — those still copy the plain string.
- `Company` - A legal entity (e.g. Taamul, NAAS). Deliberately lean — holds only identity fields (`code`, `name`, `legal_name`, `trade_licence_number`, `establishment_number`, `default_labour_jurisdiction`); per-entity rules (leave, payroll, WPS, holidays) attach as `company` FKs on the tables that already own each concept rather than living here. User-addable via the UI with no hard-coded seed list — the system is migrating from an implicit single-tenant assumption to explicit multi-entity support, one phase at a time.

**Employee History, Assignments & Approvals (`attendance/models.py`):**
- `EmployeeAssignment` - Historical "who sat where, reporting to whom" record (dual employee/remote_employee FK + `company`, `effective_from`/`effective_to`, `is_current`, `department`/`team`/`location`/`designation` as copied CharFields, `reporting_manager`/`functional_manager`, `change_type`). Replaces the lossy pattern of overwriting single fields on the employee row directly. The only mutator is `services_assignments.open_assignment()` (closes the current row, opens the next, in one transaction); overlap is enforced in `clean()` rather than a DB constraint because MySQL can't express a partial unique index. Refuses backdating before the current assignment's start or into an already-closed (possibly already-paid-against) period.
- `EmployeeTimelineEvent` - Unified per-employee chronology, deduplicated via `dedupe_key`; written by `services_timeline.record()` (best-effort — never raises).
- `ApprovalChain` / `ApprovalChainStep` - Configurable approval sequence per `request_type` + `company` (role-ordered), with a catch-all fallback when no company-specific chain exists.
- `ApprovalRequest` / `ApprovalStep` - A frozen-payload request moving through its chain. `services_approvals.submit()` refuses to auto-approve when no chain is configured (missing config is a hard error, not a silent bypass); `decide()` enforces in-order, correct-role approval. Approve ≠ apply: once fully approved, a registered "applier" function executes the change and can fail independently (recorded in `apply_error`, not swallowed) — "approved but not yet applied" is a real, visible state.
- `services_transactions.py` (`promote()`, `transfer()`, `change_manager()`, `revise_salary()`, `change_status()`) is the intended replacement for direct field edits: each freezes proposed values into an `ApprovalRequest` instead of touching the employee row immediately. Notably, `revise_salary()` creates no `SalaryStructure` row at submit time — only the approved applier does, since `get_effective_salary_structure()` selects `status='approved'` and a pending row would risk paying an unapproved amount; approving a revision does not mark the prior `SalaryStructure` row superseded (only `effective_from` ordering matters), since that would break historical payslip lookups.
- **Maturity note**: this whole engine (assignments/approvals/transactions) is a built, migration-in-progress backend with only Django Admin (mostly read-only) exposure and idempotent dry-run-by-default backfill commands (`backfill_leave_ledger`, `backfill_timeline`) — there is **no dedicated frontend UI yet** (approvals inbox, assignment history screen). The legacy direct-field-edit path (`employee_management.py`, `employee_profile.py`'s `_save_employment`) is presumably still what's actually used day to day; treat this as additive/parallel, not the enforced path, until a UI exists.

**Identity, Leave Policy & Compliance (`attendance/models.py`):**
- `PersonScopedModel` (abstract) - Dual employee/remote_employee FK with a one-XOR-other `clean()` guard; base for the detailed HR record types below.
- `EmployeeVisa`, `EmployeeDependent`, `EmployeeInsurance`, `EmployeeMedicalFitness`, `EmployeeEducation`, `EmployeeQualification`, `EmployeePreviousEmployment` - Detailed HR records (subclass `PersonScopedModel`), each optionally linked to an `EmployeeDocument`. Managed via `attendance/services_identity.py` (`renew_visa()`/`cancel_visa()` never delete/edit old rows, only flip status — a legal record for government queries). **Not yet wired into the Compliance Watchlist** — `services_compliance.watchlist()` still reads expiries from `EmployeeDocument` only; `services_identity.expiring_identity()` is a separate, additive alerting surface until the two are deliberately merged.
- `LeaveType`, `LeavePolicy` (scoped by jurisdiction/company/category), `LeavePolicyVersion` (effective-dated statutory numbers, never edited in place — new version instead), `LeaveLedgerEntry` (signed-days ledger via `attendance/services_leave_ledger.py`). **Not yet the source of truth**: `payroll.services_leave_earnings.leave_summary()` (computed live from `AnnualLeave`/`LeaveRequest`) remains authoritative; `reconcile()` just reports the gap per employee until the two agree.
- `EmployeeReturnToWork` - Records return-from-leave separately from editing `joining_date`, per a past incident where the two were conflated.

**Payroll (`payroll/models.py`):**
- `PayrollAdjustment` - Monthly incentives/reductions per employee (both in-house and remote)
- `Bank` - Bank with AED and optional INR per-account charge; `charge_for_currency()` returns the right rate
- `BankSubmission` - Per-employee-per-month submission count per bank; unique per (employee, bank, year, month)
- `DeductionEntry` - Deduction or addition (determined by `entry_type` property); can be split over N months; `installment_amount = total / split_months`; `is_active_in(year, month)` checks if a month falls in the split range
- `DeductionCarryover` - Auto-created when net salary would go negative; `overflow_amount` carries into the following month
- `ExchangeRate` - 1 AED = N units of foreign currency, stored per currency per month; to convert foreign → AED: `amount / rate`
- `GeneratedDocument` - Registry of every payslip/voucher; stable human-readable ref (`PS-XXXXX` / `PV-XXXXX`) via `ref` property
- `FrozenPayrollMonth` - Immutable JSON snapshot of a fully-computed payroll month; once frozen, dashboard serves from this snapshot instead of recalculating — freeze/unfreeze via `/payroll/api/freeze/` and `/payroll/api/unfreeze/`
- `PaidSalaryRecord` - Per-employee immutable payroll snapshot created when salary is marked as paid; stores full snapshot (attendance, deductions, commission, bank submissions, final salary) at the moment of payment — mark/unmark via `/payroll/api/mark-paid/` and `/payroll/api/unmark-paid/`. Supports **partial payment** (`amount_paid`, `payment_method`, `payment_date`, `payment_splits`): `effective_amount_paid` resolves legacy NULL rows (pre-partial-payment) to a full payment rather than misreporting them as unpaid; `is_partial` / `balance_due` are derived. `POST /payroll/api/add-payment/` (`add_partial_payment`) adds a further installment against an already-partial month without touching the locked `snapshot`/`final_salary`.
- `PayrollNote` - Append-only free-text note per employee (dual-FK), created manually via the Notes & Timeline modal
- `CommissionTierSettings` - Per-currency tiered DSA commission rule (`threshold` + `overflow_rate`); referenced by `Bank.charge_for_currency()`
- `PayrollRun` - One row per calendar month driving the Phase 9 lifecycle state machine (`draft → review → approved → locked → paid → posted`) via forward-only `advance()`; a control layer above `FrozenPayrollMonth`/`PaidSalaryRecord`, not a replacement for them. `services_payroll_rerun.reopen_run(year, month, actor, reason)` is the one sanctioned way to break the "immutable once locked" invariant — it deletes that month's `PaidSalaryRecord`s and `FrozenPayrollMonth` snapshot and resets the run to Draft, tracked via `reopened_count`/`reopened_by`/`reopened_at`/`reopen_reason` and a full pre-delete summary written to `AuditLog` (since that becomes the only remaining record). Requires a non-empty reason; doesn't touch `DeductionEntry`/`Loan`/carryovers (those are inputs, not outputs).
- `EmployeeTarget` - Monthly funded-accounts target per employee (dual-FK); achievement is derived at read time from `BankSubmission`, not stored
- `DeductionType` - Configurable deduction/addition category (replaces the old hardcoded choice list on `DeductionEntry`). Built-in types (`is_system`) can't be renamed/re-typed; a type with existing `DeductionEntry` history can't switch `entry_type` (would flip the sign of historical money) or be deleted. Managed at `/payroll/deduction-types/`.
- `DeductionRule` - A pre-entry advisory/blocking ceiling on deductions (`max_percent` of basic/gross and/or `max_amount`, scoped to one deduction code, to loans, or to everything; `enforcement` = block or warn). Evaluates to `pass`/`breach`/**`unevaluated`** (e.g. a %-of-basic rule can't evaluate for a remote employee with no `SalaryStructure`) — `unevaluated` is never silently treated as `pass`. This checks proposed/existing entries; it does **not** cap amounts during payroll calculation itself (that's an unbuilt future phase). Managed at `/payroll/deduction-limits/`.
- `Loan` / `LoanInstallment` - Interest-free salary-advance/loan tracker (statuses draft/active/settled/cancelled/on_hold). A `Loan` wraps a `Recoverable` 1:1 (`sync_recoverable()`) — `Recoverable` remains the single source of "what they owe"; `Loan` owns the repayment *schedule*. `split_principal()` rounds every installment down and dumps the remainder on the last one so installments always sum exactly to principal. Activating a loan posts one ordinary `DeductionEntry` per installment (`split_months=1` each, category `loan_repayment`) so individual months are independently waivable/skippable. `refresh_recovery()` marks an installment recovered only once its month's `PaidSalaryRecord` is paid **in full** (not partial). Managed at `/payroll/loans/`.
- `PaidHolidayDeclaration` / `PaidHolidayAward` - Admin declares a month's paid-holiday dates (Sundays auto-excluded); confirming writes one `DeductionEntry`-based award per employee at `(gross/period_days) × day_count` — the same divisor payroll uses for absence deductions, so a paid day and a deducted day are equal size by construction. `withdraw()` only removes entries for months not yet in a `PaidSalaryRecord`. Pure-commission employees are skipped (no fabricated rate). Managed at `/payroll/paid-holidays/`.
- `SundayEntitlementRecord` - Persisted per employee/year/month result of the Sunday-entitlement calculation (see `services_sunday_entitlement.py` below); `system_calculated_count` is immutable once written, HR overrides go in a separate `override_count`, both audited.
- `CommissionPlan` - Lookup table (not a hard-coded tuple) for an employee's commission-plan code; deliberately holds no rates itself, only the picklist — the commission engine owns the numbers.
- `EmployeePartnerBank` - Link table (not M2M) recording which partner banks an employee is assigned to, which is primary, and when — drives RO target-setting and the commission plan link. Dual-FK to support remote (DSA) employees, not just in-house.

### URL Structure

**Admin Panel:** `/` (upload), `/upload/multiday/` (multi-day Daily Report upload), `/upload/remote/`, `/upload/remote/monthly/`, `/report/` (in-house, plus `/report/download/` and `/report/download/employee/<id>/`), `/report/remote/` (remote, plus `/report/remote/download/` and `/report/remote/download/employee/<id>/`), `/employees/`, `/employees/<person_id>/profile/` (360° profile, plus `/employees/<person_id>/profile/compliance/reveal/`), `/compliance/` (Compliance Watchlist, plus `/compliance/export/`), `/on-duty-requests/` (early-leave/on-duty request queue), `/leave-requests/`, `/annual-leave/` (plus `/annual-leave/add/`, `/annual-leave/<id>/delete/`), `/special-shifts/` (plus `/special-shifts/add/`, `/special-shifts/<id>/update/`, `/special-shifts/<id>/delete/`)

**Employee Management:** `/employees/update/`, `/employees/bulk-update/`, `/employees/merge/`, `/employees/delete/`, `/employees/link/`, `/employees/unlink/`

**User Management (IT admin only):** `/user-management/`, `/user-management/create/`, `/user-management/<user_id>/update/`, `/user-management/<user_id>/delete/`

**Employee Portal:** `/portal/` (dashboard), `/portal/login/`, `/portal/logout/`, `/portal/change-password/`, `/portal/early-leave-request/`, `/portal/leave-request/`, `/portal/api/my-requests/`

**APIs:** `/api/attendance/update/`, `/api/remote/attendance/update/`, `/api/pending-count/`, `/api/pending-requests/`, `/request/<id>/data/`, `/request/<id>/approve/`, `/request/<id>/decline/`, `/on-duty-requests/approve-all/`, `/leave/<id>/approve/`, `/leave/<id>/reject/`, `/set-period/`

**Payroll:** `/payroll/` (comprehensive dashboard, `payroll_test_dashboard` — see below), `/payroll/old/` (legacy dashboard, `payroll_dashboard`), `/payroll/employees/` (salary setup), `/payroll/banks/` (plus API `/payroll/api/banks/` and `/payroll/api/banks/<id>/`), `/payroll/api/adjustments/`, `/payroll/api/remote-adjustments/`, `/payroll/api/submissions/<emp_type>/<id>/`, `/payroll/api/submissions/save/`, `/payroll/api/upload-submissions/` (bulk XLSX), `/payroll/api/deductions/add/`, `/payroll/api/deductions/autofill/`, `/payroll/api/recalculate/`, `/payroll/api/exchange-rate/save/`, `/payroll/api/commission-tier/save/`, `/payroll/api/freeze/`, `/payroll/api/unfreeze/`, `/payroll/api/mark-paid/`, `/payroll/api/unmark-paid/`, `/payroll/api/add-payment/` (partial payment), `/payroll/api/carryover/<id>/toggle-skip/`, `/payroll/api/employee/<emp_type>/<id>/update/`, `/payroll/payslip/<emp_type>/<id>/`, `/payroll/payslip-history/`, `/payroll/voucher/advance/`

Note: `/payroll/` and `/payroll/test/` both route to `payroll_test_dashboard` — the "test" dashboard is now the primary one. The original `payroll_dashboard` view lives on at `/payroll/old/` for reference/rollback.

**Payroll — Phase 9–D (each on its own `views_*.py` module, see [Payroll Phase Modules](#payroll-phase-modules)):** `/payroll/run/<year>/<month>/` (Payroll Run lifecycle), `/payroll/performance/<year>/<month>/` (Team Performance), `/payroll/profitability/<year>/<month>/`, `/payroll/management/` and `/payroll/management/<year>/<month>/` (Management Dashboard), `/payroll/audit-log/`, `/payroll/api/notes/<emp_type>/<id>/` and `/payroll/api/notes/add/` (Notes & Timeline), `/payroll/range-report/` (Range/Annual Report), `/payroll/api/debug/snapshot/` (**temporary** — see note below)

**Payroll — deduction/loan/holiday/headcount modules** (see [Newer Payroll Modules](#newer-payroll-modules) — these reuse the code-comment label "Phase 2/3/4", a **separate, unrelated numbering scheme** from the "Phase 9–D" modules above): `/payroll/deduction-types/` + `/payroll/api/deduction-types/{save,toggle,delete}/`, `/payroll/loans/` + `/payroll/api/loans/{preview,save,activate,cancel,waive,delete}/`, `/payroll/deduction-limits/` + `/payroll/api/rules/{save,toggle,delete}/`, `/payroll/paid-holidays/` + `/payroll/api/paid-holidays/{preview,confirm,withdraw}/`, `/payroll/api/paid-leave/` (read-only report), `/payroll/headcount/` (supports `?format=csv`)

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
- `attendance/audit.py` - `log_audit()` / `diff_fields()` helpers for writing to `AuditLog`. See [Audit Logging](#audit-logging)
- `attendance/management/commands/recalculate_summaries.py` - Management command to rebuild monthly summaries
- `attendance/management/commands/backfill_leave_ledger.py` / `backfill_timeline.py` - Idempotent, dry-run-by-default (`--apply` to write) backfills for `LeaveLedgerEntry` and `EmployeeTimelineEvent` — see [Employee History, Assignments & Approvals](#data-models)
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

**Effective-dated history (3-tier)**: `Employee.salary_cycle_start_day`/`RemoteEmployee.salary_cycle_start_day` remain a live "current value" field (still what every non-date-aware read site shows), but the actual pay period for a given calendar month is computed by `attendance/services_salary_cycle.py:get_employee_pay_period(employee, year, month)` — the single funnel point consumed by `payroll/services_payroll_engine.py:get_pay_period()` and the other pay-period call sites in `payroll/views.py` (`download_payslip`, `payroll_test_dashboard`, `mark_paid_salary`). Three tables feed it, each keyed by an exact `effective_date` (not just a month), highest priority first: `SalaryCycleHistory` (one employee's own override) → `SalaryCycleGroupDefault` (one payroll group's cycle — see below) → `SalaryCycleDefault` (company-wide, e.g. "from 2026-07-21 use day 21") → the legacy field. A tier with any rows governs exclusively from its earliest row onward, ignoring later changes at a broader tier (an override means "this person/group is on their own schedule now"). With zero rows anywhere, resolution is identical to reading the field directly.

**Groups**: `SalaryCycleGroupDefault.group` is one of 4 keys matching `payroll/services_payroll_engine.py:SECTION_LABELS` — `admin_inhouse`, `admin_remote`, `sales_fixed`, `sales_perf` (the payroll dashboard's 5th "section", `sales_perf_method2`, is deliberately excluded: it's the same remote employees as `sales_perf` recalculated for display, not a separate population). Membership is computed live, never stored, by `classify_employee_section(employee)` / bulk `classify_employees_bulk(employees)` (department / `is_fixed_salary` / `tcr_id` dedup — the same rules `select_employees()` uses to build the dashboard sections).

Day-precision matters: a cycle change is clipped, not just switched, so no calendar day is ever paid twice or skipped. `get_employee_pay_period()` computes each month's period end independently (the natural shape of whichever cycle governs by that month's last day, clipped early if another change starts before that natural end), then sets that month's period start to exactly one day after the *previous* month's computed end — guaranteeing every day belongs to exactly one period regardless of what day of the month `effective_date` falls on. A change that doesn't land on a "clean" boundary (e.g. switching to a 21st-cycle on the 15th) produces one shorter- or longer-than-normal transition month rather than a gap or overlap. `get_employee_pay_periods_bulk()` is the batched variant for a whole payroll run (a fixed small number of queries regardless of employee count).

Mutations go through `set_employee_cycle_override()` / `set_group_cycle()` / `set_default_cycle()` only (upsert by `(person-or-group-or-default, effective_date)`; never blocks on an already-paid/frozen month, just attaches a non-fatal `.warning` to the returned row — a hard block here turned out to constantly trip on ordinary edits in an active system, since almost any past month has *some* paid employee).

**Where it's managed**: the **Pay Cycle Management** page (`/payroll/pay-cycle/`, `payroll/views_salary_cycle.py:pay_cycle_management`, gated `'payroll'` section, linked from the sidebar's Compensation group) has two tabs — **Groups** (all 4 groups, each with its own add/delete timeline) and **Company Default**. Per-employee overrides stay in the Employee Management edit popup's Payroll tab and the Employee Profile → Bank section — both now display a 3-source timeline (Employee override / Group default / Company default) alongside the add form. The legacy `/payroll/employees/` template is dead (that URL redirects to `/employees/`) and was not kept in sync with any of this.

### Remote Attendance Inline Editing

Admins can edit remote call records directly from the remote attendance report. The new API endpoint `POST /api/remote/attendance/update/` (`update_remote_attendance` in `api.py`) accepts `employee_id`, `date`, `talk_minutes`, and `answered_calls`. It upserts a `RemoteCallRecord` and returns the recalculated `attendance_status`. The remote report template renders an edit icon on each day cell and a modal form wired to this endpoint.

### Audit Logging

`attendance.AuditLog` is a cross-app audit trail written to by both apps. `log_audit(actor, action, instance, changes=None, note='')` (`attendance/audit.py`) never raises — failures are caught and logged so a broken audit write can never block the real mutation.

Two wiring styles are used, depending on whether the call site has a real `request.user`:
- **Explicit calls** (preferred, real actor attribution): `attendance/views/employee_profile.py` (salary/cost/recoverable saves), `attendance/admin.py` (`save_model`/`delete_model` overrides), `payroll/models.py` (`PayrollRun.advance()`).
- **Signal fallback** (`payroll/signals.py`, wired via `PayrollConfig.ready()`): `pre_save`/`post_save`/`post_delete` on `DeductionEntry` only, logged as `actor='system'`, because that model is edited exclusively from the legacy `payroll/views.py` monolith with no call site that has a real user in scope.

Browse the trail at `/payroll/audit-log/` (`payroll/views_audit.py`, filterable by model/actor/action/date-range).

### Employee 360° Profile

`attendance/views/employee_profile.py:employee_profile(request, person_id)` (`/employees/<person_id>/profile/`) renders a full profile: onboarding checklist, employment/salary/employer-cost history, documents, recoverables sub-ledger, and a 12-month performance trend (lazily imported from `payroll.services_performance.person_trend` inside the function body to avoid an import cycle between the two apps).

POST with `?section=<name>` dispatches to one of 10 section-specific JSON save handlers (`personal`, `contact`, `identity`, `employment`, `salary`, `bank`, `cost`, `document`, `recoverable`, `onboarding`) via `_handle_section_post`. Notably: `_save_employment` auto-diffs tracked fields into `EmploymentHistory`; `_save_salary` supersedes the prior approved `SalaryStructure`, syncs `Employee.salary`/`currency`, and audits the change.

### User Management

`attendance/views/user_management.py` manages Django auth `User` accounts (create/update/delete + paired `UserProfile`) for IT admins, gated by `it_admin_required` (narrower than the general `superuser_required` used elsewhere — requires `UserProfile.is_it_admin`). Guards against removing/self-deleting the last active superuser. Per-user section access is stored on `UserProfile.allowed_sections`/`sections_restricted` against `NAV_SECTIONS`/`NAV_SECTION_KEYS` (`attendance/views/utils.py`) — this is the nav-based permission system that gates access to sections like `'payroll'`, `'management'`, and `'audit_log'` across both apps. `UserProfile.role` (see [Compliance Watchlist](#compliance-watchlist)) is a separate field-level permission axis and is not managed by the section-grant UI in the same way.

### Compliance Watchlist

`attendance/views/compliance.py` (`/compliance/`, plus CSV export at `/compliance/export/`) is a **read-only** cross-staff dashboard: 90/60/30-day expiry bands for tracked documents, plus compliance-review and probation-review due-dates, gated by the `'employees'` section grant (same as Employee Management).

Visibility is decided entirely server-side by `attendance/compliance_access.py`, keyed off `UserProfile.role` (`hr_admin`/`exec_director`/`manager`/`it`) rather than sections — a value the viewer's role may not see is never put into the template context at all (not hidden with CSS/`{% if %}`, which would still leak it into page source). Three questions are answered independently per field group: `can_view` (may this role know the field exists), `is_masked` (Emirates ID/IBAN are masked for everyone who can see them at all), `can_reveal` (may this role request the full value). Revealing a masked value goes through `POST /employees/<person_id>/profile/compliance/reveal/` (`employee_profile.py:compliance_reveal`), which writes an `AuditLog` **view** entry on every call — reading an identity number is treated as an event that must be answerable later, not just a display toggle.

`services_compliance.py:watchlist()` currently reads document expiries from `EmployeeDocument` only — it does **not** yet consult the newer `EmployeeVisa`/`EmployeeInsurance`/etc. tables (see [Identity, Leave Policy & Compliance](#data-models)); that's deliberate, temporary duplication pending a reconciliation pass.

### Payroll Phase Modules

Newer payroll functionality (Phases 9–D) is deliberately kept out of the `payroll/views.py` monolith, each phase in its own `views_*.py` + optional `services_*.py` pair. All follow the same pattern: `@login_required` + `@user_passes_test(section_required('<section>'), login_url='/report/')`, deferred imports inside function bodies, and a locally duplicated `MONTH_NAMES` list per file rather than a shared constant.

- **Phase 9 — Payroll Run** (`views_payroll_run.py`, `/payroll/run/<year>/<month>/`): renders the `PayrollRun` lifecycle page with an "exception centre" (`_build_exception_report`: blockers for missing salary structure/bank details/exchange rate; warnings for inactive employees with active deductions, orphaned `Recoverable`s). POST `action=advance` moves the run forward one stage; `action=save_notes` saves free text.
- **Phase 10 — Team Performance** (`services_performance.py` + `views_performance.py`, `/payroll/performance/<year>/<month>/`): aggregates `BankSubmission` vs `EmployeeTarget`. `month_performance()` returns rows + team rollups; `person_trend()` returns a 12-month series (consumed by the Employee 360° Profile); `status_for_pct()` buckets into achieved/near/below/no_target.
- **Phase 11 — Profitability** (`services_profitability.py` + `views_profitability.py`, GET-only, `/payroll/profitability/<year>/<month>/`): Total Cost / Contribution / ROI% / Cost-per-account per agent in AED (Cost = Salary + `EmployerCostSetup` + Commission; revenue from `Bank.revenue_per_account`). Agents whose currency has no `ExchangeRate` for the month are flagged `fx_missing=True` and excluded from AED totals rather than zeroed. Tiered INR/NPR commission is intentionally not modeled here (v1, flat rates only).
- **Phase 12 — Management Dashboard** (`services_management.py` + `views_management.py`, GET-only, `/payroll/management/` redirects to the current month, `/payroll/management/<year>/<month>/`): rolls up Phases 9–11 into one executive snapshot (`management_snapshot()`) — KPIs, top/bottom performers and contributors, an alerts list, and data-health metrics. Gated by a distinct `'management'` section grant. Does not duplicate business math from the other phases.
- **Phase 13 — Audit Log** (`views_audit.py`, GET-only, `/payroll/audit-log/`): see [Audit Logging](#audit-logging).
- **Phase C — Notes & Timeline** (`views_notes.py`, `/payroll/api/notes/...`): builds a merged per-employee timeline from `PayrollNote` rows, `DeductionEntry` audit events (matched via `AuditLog.object_repr` string-prefix — a fragile coupling worth knowing about if audit log formatting ever changes), `PaidSalaryRecord` mark-as-paid events, and `DeductionCarryover` create/skip events.
- **Phase D — Range/Annual Report** (`views_range_report.py`, GET-only, `/payroll/range-report/`): aggregates already-paid `PaidSalaryRecord.snapshot` JSON across a date range/annual/multi-month/since-joining selection. Deliberately reports only on locked snapshots rather than recomputing, so it can never drift from what was actually paid; each row shows "N of M months included" to surface gaps instead of hiding them.
- **`services.py`** (shared, no phase of its own): `get_effective_salary_structure()`, currency conversion helpers (`convert_amount()`, `convert_employee_deduction_currency()`) used by both the profile view and payroll views.
- **`views_debug.py`** (`/payroll/api/debug/snapshot/`) — **temporary, read-only diagnostic** for a Phase D investigation; the file's own docstring and the `urls.py` comment say to delete it (and its one URL line) once the investigation is done. Don't build on top of it.

### Newer Payroll Modules

A second, later batch of `views_*.py`/`services_*.py` pairs, none of which replace anything above — all additive, all built on **`services_payroll_engine.py`**, a read-only wrapper around `payroll/views.py`'s private `_get_inhouse_payroll_row`/`_get_sales_payroll_row`/`_attach_gross_breakdown` (`get_pay_period()`, `calculate_employee_payroll()`, `select_employees()`, `build_all_sections()`). New payroll code should import from this module, not reach into `payroll.views` private helpers directly — it's the intended (partial) extraction seam out of the monolith. `select_employees()` also codifies, in one place, the dashboard's 5 sections (`admin_inhouse`, `admin_remote`, `sales_fixed`, `sales_perf`, `sales_perf_method2`) and the `tcr_id` in-house-wins dedup rule.

**Note on numbering**: these modules' own code comments label themselves "Phase 2", "Phase 3", "Phase 4" etc. — this is a **separate, unrelated numbering scheme** from "Phase 9–D" above (both exist in the same codebase, numbered independently by whoever wrote each batch).

- **Deduction Master ("Phase 2")** (`views_deduction_types.py`, `/payroll/deduction-types/`): admin CRUD for `DeductionType`, the category picklist the rest of payroll draws from. The one setting most likely to silently break itemized-vs-total consistency: `rolls_up_to_other` determines which dashboard column a type's amounts land in.
- **Loans ("Phase 3")** (`services_loans.py` + `views_loans.py`, `/payroll/loans/`): see `Loan`/`LoanInstallment` in [Data Models](#data-models) above.
- **Deduction Rules ("Phase 4")** (`services_deduction_rules.py` + `views_rules.py`, `/payroll/deduction-limits/`): see `DeductionRule` in [Data Models](#data-models) above. `check_proposed()` is the entry point a deduction-creation form should call before saving; `check_month()` drives the pre-payroll breach-check screen.
- **Leave Earnings** (`services_leave_earnings.py`, library only, no URL yet): UAE Art. 29 leave-value calculator (accrual/balance/encashment), separate from payroll's day-to-day deductions. Computes **two** encashment figures side by side — `encashment_policy` (company rule, defaults to matching `AnnualLeave.salary_percentage`) vs `encashment_statutory` (Art. 29 floor: 100% of *basic*, not gross) — and deliberately returns `None`, never `0`, when the basic salary is unknown, since a fabricated basic could become a real termination payout.
- **Paid Leave report** (`services_paid_leave.py`, `POST /payroll/api/paid-leave/`, view in `views_paid_holidays.py:paid_leave`): read-only — computes nothing new, just surfaces what the payroll engine already did for protected-absence deductions and annual-leave compensation as a per-employee report.
- **Sunday Entitlement** (`services_sunday_entitlement.py` + `services_sunday_entitlement_db.py`, library + model only — **no URL/view wired up yet**): a deliberate two-layer split, not competing implementations. `services_sunday_entitlement.py` is a pure, Django-free function (`calculate_sunday_entitlement`) taking dates in and returning a per-Sunday verdict; `services_sunday_entitlement_db.py` is a thin Django wrapper (`entitlement_for`, `save_entitlement`, `apply_override`) persisting into `SundayEntitlementRecord`. Key rule: a Sunday counts only *strictly after* joining/rejoining (`>`, never `>=`).
- **Paid Holidays** (`services_paid_holidays.py` + `views_paid_holidays.py`, `/payroll/paid-holidays/`): see `PaidHolidayDeclaration`/`PaidHolidayAward` in [Data Models](#data-models) above.
- **Headcount** (`services_headcount.py` + `views_headcount.py`, `/payroll/headcount/?format=csv`): pure read-only report comparing "who payroll shows today" (`is_active=True`, same dedup as `select_employees()`) against "who was actually employed in month X" (by `joining_date`/`leaving_date` over each employee's own pay period). Surfaces `paid_but_not_employed` — a `PaidSalaryRecord` existing for someone outside their employment dates — as a genuine payroll-error signal.
- **Payroll Rerun** (`services_payroll_rerun.py`, no dedicated URL — called from `views_payroll_run.py`): see `PayrollRun.reopen_run` note in [Data Models](#data-models) above.

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

The core payroll calculation (dashboards, deductions, commission, freeze/mark-paid, payslips) lives in the `payroll/views.py` monolith, described below. Newer functionality (Phases 9–D: payroll run lifecycle, team performance, profitability, management dashboard, audit log, notes, range report; plus a later, separately-numbered batch covering deduction types/rules, loans, paid holidays, Sunday entitlement, and headcount) is intentionally kept out of that file — see [Payroll Phase Modules](#payroll-phase-modules) and [Newer Payroll Modules](#newer-payroll-modules). Every model (payroll and attendance) uses the dual-FK pattern: `employee` (in-house) and `remote_employee` (remote) — exactly one must be set; `clean()` enforces this.

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

**Partial payment**: Mark-as-Paid is no longer strictly all-or-nothing. `PaidSalaryRecord` carries `amount_paid`/`payment_method`/`payment_date`/`payment_splits` alongside the locked `final_salary`/`snapshot` — the two never change once written; only the disbursement fields do. `POST /payroll/api/add-payment/` records an additional installment against a still-partial month (appends to `payment_splits`, bumps `amount_paid`) without touching the locked figures. See `Loan.refresh_recovery()` in [Data Models](#data-models) for one consumer of `is_partial`: a loan installment is only marked recovered once its month is paid in full.

## Git Workflow

Main branch: `main`
Remote: `git@github.com:yadhumanikandan/attendance_system.git`

## Production Deployment

Deployed on Ubuntu 24.04 with Gunicorn, MySQL, systemd service (`attendance.service`), and WhiteNoise. See `DEPLOYMENT.md` for complete setup guide.

This is the **port 8000 main production app** at `/var/www/attendance` — a separate deployment from this repo's working directory. **Off-limits by default; see the warning under [Production Server](#production-server).** The process below is documented for reference only — do not run it unless the user has explicitly named port 8000 / `attendance.service` / `/var/www/attendance` and confirmed they want it updated.

Update process (port 8000 only — requires explicit user confirmation):
```bash
cd /var/www/attendance
source venv/bin/activate
git pull origin main
DJANGO_SETTINGS_MODULE=attendance_project.settings.production python manage.py migrate
DJANGO_SETTINGS_MODULE=attendance_project.settings.production python manage.py collectstatic --noinput
sudo systemctl restart attendance
```

For routine deploy work in this repo, use the port 8001 staging process instead (`attendance-staging.service`, working dir `/home/ubuntu/attendance`, DB `attendance_db_staging`) — see [Production Server](#production-server).

