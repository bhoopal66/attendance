# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Django-based attendance management system for TCR with dual employee tracking:
- **In-house employees**: Tracked via biometric attendance machines (uploaded as XLS files)
- **Remote employees**: Tracked via phone call statistics (uploaded as CSV files)

The system includes an admin panel for attendance management and an employee portal for viewing attendance and submitting leave requests.

## Development Setup

### Local Development (SQLite)
```bash
# Activate virtual environment
source venv/bin/activate

# Run development server on port 8080
python manage.py runserver 8080

# Run migrations
python manage.py migrate

# Create superuser
python manage.py createsuperuser
```

### Production Setup (MySQL)
Uses `settings_production.py` with MySQL. See `DEPLOYMENT.md` for full Ubuntu deployment guide.

```bash
# Run with production settings
DJANGO_SETTINGS_MODULE=attendance_project.settings_production python manage.py migrate
DJANGO_SETTINGS_MODULE=attendance_project.settings_production gunicorn --bind 0.0.0.0:8000 attendance_project.wsgi:application
```

### Environment Configuration
Copy `.env.example` to `.env` and configure:
- `SECRET_KEY`: Django secret key
- `ALLOWED_HOSTS`: Comma-separated list of allowed hosts
- `DB_PASSWORD`: MySQL password (production only)

## Architecture

### Apps Structure

**attendance/** - Main app for attendance tracking and employee management
- `models.py`: Data models (Employee, RemoteEmployee, AttendanceRecord, RemoteCallRecord, requests)
- `views/`: Modular view structure
  - `upload.py`: XLS/CSV file upload and processing
  - `reports.py`: Attendance reports for in-house employees
  - `downloads.py`: Excel report generation
  - `employee_portal.py`: Employee portal (login, attendance view, leave requests)
  - `employee_management.py`: Employee CRUD operations
  - `leave_management.py`: Leave request approval workflow
  - `api.py`: JSON API endpoints for frontend interactions
- `templates/attendance/`: HTML templates with modern purple-themed UI
- `static/attendance/`: CSS and JavaScript files

**payroll/** - Payroll management (separate module)

### Data Models

#### Employee Tracking Models
- `BaseEmployee` (abstract): Shared fields for both employee types
- `Employee`: In-house employees with `person_id` from biometric machines
- `RemoteEmployee`: Remote employees with `extension_id` from phone system
- `AttendanceRecord`: Daily attendance for in-house (first_in, last_out, work_duration)
- `RemoteCallRecord`: Daily call stats for remote (answered_calls, talk_duration, auto-calculated status)
- `MonthlySummary` / `RemoteMonthlySummary`: Monthly aggregates

#### Request Management Models
- `EarlyLeaveRequest`: On-duty/field visit requests with approval workflow
- `LeaveRequest`: 4 types (sick, medical, annual, casual) with document upload support
- `Holiday`: Custom holidays (Sundays are auto-detected)

#### Employee Lookup Strategy (Critical)
When processing XLS uploads, the system uses a **3-tier lookup strategy** to handle duplicate names:
1. Try exact match: `person_id` + `name`
2. If multiple matches: Use most recently updated employee
3. If no match: Create new employee record

This prevents duplicate employee creation when names match but IDs differ.

### URL Structure

**Admin Panel:**
- `/` - Upload attendance files (XLS for in-house, CSV for remote)
- `/report/` - In-house employee attendance reports
- `/report/remote/` - Remote employee call statistics
- `/employees/` - Employee management
- `/leave-requests/` - Leave request approval
- `/admin/` - Django admin

**Employee Portal:**
- `/portal/` - Employee dashboard with calendar view
- `/portal/login/` - Employee login (uses `portal_password` field)
- `/portal/early-leave-request/` - Submit on-duty requests
- `/portal/leave-request/` - Submit leave requests
- `/portal/api/my-requests/` - API for fetching employee's requests

**API Endpoints:**
- `/api/attendance/update/` - Update attendance records (admin)
- `/api/pending-requests/` - Get pending requests count
- `/request/<id>/approve/` - Approve early leave request
- `/leave/<id>/approve/` - Approve leave request

### Frontend Architecture

Modern, responsive UI with purple theme (`--primary-color: #4F46E5`):
- **Admin Panel**: Collapsible employee cards with monthly calendar views
- **Employee Portal**: Sidebar navigation with dashboard showing attendance calendar and request history
- **Real-time Updates**: Periodic polling for pending requests (30s interval)
- **Modals**: AJAX-based approval workflows without page reloads

Key CSS files:
- `base.css`: Shared styles, navigation, filter components (purple theme, card designs)
- `report.css`: Admin calendar view (optimized for smaller cells, readable text)
- `employee_portal.css`: Employee portal sidebar and calendar
- `upload.css`, `employee_management.css`, `leave_management.css`: Page-specific styles

Calendar visibility optimizations:
- Admin: Smaller cells (65px) with readable text (0.7-0.95rem, bold weights)
- Portal: Standard cells (90px) with proper overflow handling
- All calendars use `line-height: 1.2` and `overflow: hidden` to prevent text bleeding

### Attendance Upload Processing

**In-house (XLS):**
1. Parse XLS with `openpyxl`
2. Lookup employee by `person_id` using 3-tier strategy
3. Create/update `AttendanceRecord` (first_in, last_out, work_duration)
4. Calculate monthly summary (working_days, late_days, half_days)
5. Merge with approved early leave requests

**Remote (CSV):**
1. Parse CSV call statistics
2. Lookup employee by `extension_id`
3. Create/update `RemoteCallRecord`
4. Auto-calculate attendance status based on talk duration:
   - Mon-Thu: <45min=Absent, 45-89min=Half, ≥90min=Present
   - Friday: <30min=Absent, 30-59min=Half, ≥60min=Present
   - Saturday: ≤20min=Absent, 21-44min=Half, ≥45min=Present

### Authentication

**Admin Users:**
- Standard Django authentication (`django.contrib.auth`)
- Login: `/login/`
- Redirect: `/report/` after login

**Employees (Portal):**
- Custom authentication using `portal_password` field (hashed with `make_password`)
- Login: `/portal/login/`
- Session-based with `employee_id` stored in session
- No Django User objects created

### Static Files Handling

Development: Files served from `attendance/static/`
Production: Use WhiteNoise middleware + `collectstatic`

CSS versioning in templates: `?v=X` query params for cache busting

## Common Tasks

### Adding a New Employee
```python
from attendance.models import Employee
from django.contrib.auth.hashers import make_password

emp = Employee.objects.create(
    person_id="12345",
    name="John Doe",
    email="john@example.com",
    portal_password=make_password("password123"),
    is_active=True
)
```

### Uploading Attendance Files
Admin panel UI handles this, but programmatically:
```python
from attendance.views.upload import process_attendance_file
process_attendance_file(file_path)  # XLS for in-house
```

### Generating Reports
Reports are generated on-demand via views, downloaded as Excel files using `openpyxl`.

### Database Migrations
```bash
# Development
python manage.py makemigrations
python manage.py migrate

# Production
DJANGO_SETTINGS_MODULE=attendance_project.settings_production python manage.py migrate
```

### Updating Production
```bash
cd /var/www/attendance
source venv/bin/activate
git pull origin main
DJANGO_SETTINGS_MODULE=attendance_project.settings_production python manage.py migrate
DJANGO_SETTINGS_MODULE=attendance_project.settings_production python manage.py collectstatic --noinput
sudo systemctl restart attendance
```

## Critical Implementation Details

### Duplicate Employee Handling
The upload process uses a **3-tier lookup strategy** to prevent duplicate employee creation:
1. Exact match on `person_id` + `name`
2. If multiple matches exist, use the most recently updated employee
3. If no match, create new employee

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
Status auto-calculated on save via `calculate_attendance_status()` based on talk duration and weekday.

### Leave Request Workflow
1. Employee submits request via portal
2. Admin reviews in `/leave-requests/`
3. Admin can approve with custom `approved_days` (can be less than requested)
4. Document upload required for sick/medical leave
5. Approved leaves appear in attendance calendar

### Early Leave Request Workflow
1. Employee submits with destination, customer name, times
2. Admin reviews existing attendance data in modal
3. Admin approves with optional `approved_first_in`/`approved_last_out` times
4. These times are merged with biometric data during next upload
5. Used for field visits, customer meetings, etc.

## Design System

Purple-themed modern UI based on CSS variables:
- Primary: `#4F46E5` (purple)
- Success: `#10B981` (green)
- Warning: `#F59E0B` (orange)
- Danger: `#EF4444` (red)
- Info: `#3B82F6` (blue)
- Accent Purple: `#8B5CF6`

Components:
- Cards with rounded corners (16-20px)
- Gradient buttons with shadow effects
- Calendar with color-coded status indicators
- Modals with gradient headers
- Sidebar navigation (employee portal)

## Known Patterns

### AJAX Form Submissions
Most admin actions use AJAX to avoid page reloads:
```javascript
fetch(url, {
    method: 'POST',
    headers: {'X-CSRFToken': csrfToken},
    body: JSON.stringify(data)
})
```

### Employee Portal Session Management
```python
# Check if employee logged in
if 'employee_id' not in request.session:
    return redirect('employee_login')
```

### Calendar Rendering
Both admin and portal use similar calendar grid structure:
```html
<div class="calendar-grid">
    <div class="calendar-day status-{status}">
        <div class="day-number">{day}</div>
        <div class="work-hours">{hours}h</div>
        <div class="time-info">{in_time} - {out_time}</div>
    </div>
</div>
```

## Git Workflow

Main branch: `main`
Remote: `git@github.com:yadhumanikandan/attendance_system.git`

Always include co-authorship in commits:
```
Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>
```

## Production Deployment

Deployed on Ubuntu 24.04 with:
- Gunicorn WSGI server
- MySQL database
- Systemd service (`attendance.service`)
- WhiteNoise for static files

See `DEPLOYMENT.md` for complete production setup guide.
