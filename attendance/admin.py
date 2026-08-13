from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from .models import (
    Employee, AttendanceRecord, MonthlySummary, ShiftHistory,
    RemoteEmployee, RemoteCallRecord, RemoteMonthlySummary,
    Holiday, EarlyLeaveRequest, LeaveRequest, UserProfile,
    # Phase 2 — Master Data
    Department, Team, Location, DesignationMaster,
    # Phase 4 — Employment History
    EmploymentHistory,
    # Phase 5 — Salary Structure
    SalaryStructure,
    # Phase 6 — Employer Cost
    EmployerCostSetup,
    # Phase 7 — Employee Documents
    EmployeeDocument,
    # Phase 8 — Recoverable Sub-Ledger
    Recoverable,
    # Phase 13 — Audit Log
    AuditLog,
)
from .audit import log_audit


class ShiftHistoryInline(admin.TabularInline):
    model = ShiftHistory
    extra = 1
    ordering = ['-effective_from']


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = 'IT Admin Settings'


class CustomUserAdmin(UserAdmin):
    inlines = (UserProfileInline,)
    list_display = UserAdmin.list_display + ('is_it_admin_flag',)

    def is_it_admin_flag(self, obj):
        return getattr(obj, 'profile', None) and obj.profile.is_it_admin
    is_it_admin_flag.boolean = True
    is_it_admin_flag.short_description = 'IT Admin'


admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)


def _it_admin_only_has_permission(request):
    """Restrict Django Admin access to IT Admins only.

    Overrides AdminSite.has_permission (normally is_active + is_staff) so that
    superuser status alone no longer grants Django Admin login — only the
    is_it_admin flag does. Mirrors the gate used for the custom User
    Management page (see views/utils.py:it_admin_required).
    """
    user = request.user
    if not (user.is_active and user.is_staff):
        return False
    profile = getattr(user, 'profile', None)
    return bool(profile and profile.is_it_admin)


admin.site.has_permission = _it_admin_only_has_permission


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ('person_id', 'name', 'employment_status', 'is_active', 'email', 'department', 'shift_start', 'shift_end')
    list_filter = ('is_active', 'employment_status', 'department', 'location', 'team')
    search_fields = ('person_id', 'name', 'email', 'tcr_id', 'national_id', 'passport_number')
    ordering = ('name',)
    autocomplete_fields = ('reporting_manager',)
    inlines = [ShiftHistoryInline]
    fieldsets = (
        (None, {
            'fields': ('person_id', 'name', 'tcr_id')
        }),
        ('Employment Lifecycle', {
            'fields': ('employment_status', 'is_active', 'joining_date', 'notice_date', 'relieving_date', 'leaving_date'),
            'description': 'Track the employee lifecycle stage. Use Relieved/Absconded + leaving_date when employee exits.',
        }),
        ('Organisation', {
            'fields': ('department', 'location', 'team', 'designation', 'reporting_manager'),
        }),
        ('Current Shift (fallback if no Shift History)', {
            'fields': ('shift_start', 'shift_end'),
            'description': 'Used only when no Shift History entries exist for this employee.',
            'classes': ('collapse',),
        }),
        ('Portal Login', {
            'fields': ('email', 'portal_password'),
            'description': 'Set email and password for employee self-service portal access.',
        }),
        ('Payroll Settings', {
            'fields': ('salary', 'currency', 'payroll_type', 'is_fixed_salary', 'visa_provider', 'salary_cycle_start_day'),
            'description': 'Pay period start day: 21 = 21st of prev month to 20th of current month; 1 = calendar month.',
            'classes': ('collapse',),
        }),
        ('Personal Information', {
            'fields': ('phone', 'date_of_birth', 'gender', 'blood_group', 'profile_photo'),
            'classes': ('collapse',),
        }),
        ('Emergency Contact', {
            'fields': ('emergency_contact_name', 'emergency_contact_phone'),
            'classes': ('collapse',),
        }),
        ('Identity Documents', {
            'fields': ('national_id', 'passport_number'),
            'classes': ('collapse',),
        }),
        ('Bank Details', {
            'fields': ('bank_name', 'bank_account_number', 'bank_routing_code'),
            'classes': ('collapse',),
        }),
        ('Onboarding', {
            'fields': ('onboarding_checklist',),
            'classes': ('collapse',),
        }),
    )

    def save_model(self, request, obj, form, change):
        # Hash password if it was changed and doesn't look like a hash
        if obj.portal_password and not obj.portal_password.startswith('pbkdf2_'):
            from django.contrib.auth.hashers import make_password
            obj.portal_password = make_password(obj.portal_password)
        super().save_model(request, obj, form, change)


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ('employee', 'date', 'first_in', 'last_out', 'work_duration')
    list_filter = ('date', 'employee')
    search_fields = ('employee__name', 'employee__person_id')
    ordering = ('-date', 'employee__name')
    date_hierarchy = 'date'


@admin.register(MonthlySummary)
class MonthlySummaryAdmin(admin.ModelAdmin):
    list_display = ('employee', 'year', 'month', 'working_days', 'leave_days', 'late_days', 'half_days')
    list_filter = ('year', 'month', 'employee')
    search_fields = ('employee__name', 'employee__person_id')
    ordering = ('-year', '-month', 'employee__name')


@admin.register(ShiftHistory)
class ShiftHistoryAdmin(admin.ModelAdmin):
    list_display = ('employee', 'shift_start', 'shift_end', 'effective_from')
    list_filter = ('employee', 'effective_from')
    search_fields = ('employee__name', 'employee__person_id')
    ordering = ('-effective_from', 'employee__name')
    date_hierarchy = 'effective_from'


# ============================================
# Remote Employee Admin
# ============================================

@admin.register(RemoteEmployee)
class RemoteEmployeeAdmin(admin.ModelAdmin):
    list_display = ('extension_id', 'name', 'is_active', 'email', 'department')
    list_filter = ('is_active', 'department', 'location', 'team')
    search_fields = ('extension_id', 'name', 'email')
    ordering = ('name',)
    fieldsets = (
        (None, {
            'fields': ('extension_id', 'name')
        }),
        ('Employment Status', {
            'fields': ('is_active', 'joining_date', 'leaving_date'),
            'description': 'Mark as inactive when employee leaves. Their historical records will be preserved.'
        }),
        ('Portal Login', {
            'fields': ('email', 'portal_password'),
            'description': 'Set email and password for employee self-service portal access.'
        }),
        ('Organization', {
            'fields': ('department', 'location', 'team'),
        }),
        ('Additional Information', {
            'fields': ('phone',),
            'classes': ('collapse',)
        }),
        ('Payroll Settings', {
            'fields': ('salary_cycle_start_day',),
            'description': 'Pay period start day. 21 = 21st of prev month to 20th of current month; 1 = calendar month (1st to last day).',
            'classes': ('collapse',)
        }),
    )

    def save_model(self, request, obj, form, change):
        # Hash password if it was changed and doesn't look like a hash
        if obj.portal_password and not obj.portal_password.startswith('pbkdf2_'):
            from django.contrib.auth.hashers import make_password
            obj.portal_password = make_password(obj.portal_password)
        super().save_model(request, obj, form, change)


@admin.register(RemoteCallRecord)
class RemoteCallRecordAdmin(admin.ModelAdmin):
    list_display = ('employee', 'date', 'total_talk_duration', 'attendance_status', 'answered_calls')
    list_filter = ('date', 'attendance_status', 'employee')
    search_fields = ('employee__name', 'employee__extension_id')
    ordering = ('-date', 'employee__name')
    date_hierarchy = 'date'
    readonly_fields = ('attendance_status',)  # Auto-calculated


@admin.register(RemoteMonthlySummary)
class RemoteMonthlySummaryAdmin(admin.ModelAdmin):
    list_display = ('employee', 'year', 'month', 'present_days', 'half_days', 'absent_days')
    list_filter = ('year', 'month', 'employee')
    search_fields = ('employee__name', 'employee__extension_id')
    ordering = ('-year', '-month', 'employee__name')


# ============================================
# Holiday Admin
# ============================================

@admin.register(Holiday)
class HolidayAdmin(admin.ModelAdmin):
    list_display = ('date', 'name')
    list_filter = ('date',)
    search_fields = ('name',)
    ordering = ('-date',)
    date_hierarchy = 'date'


# ============================================
# Early Leave / On Duty Request Admin
# ============================================

@admin.register(EarlyLeaveRequest)
class EarlyLeaveRequestAdmin(admin.ModelAdmin):
    list_display = ('get_employee_name', 'request_date', 'destination', 'customer_name', 'status', 'created_at')
    list_filter = ('status', 'request_date', 'created_at')
    search_fields = ('employee__name', 'remote_employee__name', 'destination', 'customer_name')
    ordering = ('-created_at',)
    date_hierarchy = 'request_date'
    readonly_fields = ('created_at', 'reviewed_at')
    
    fieldsets = (
        ('Employee', {
            'fields': ('employee', 'remote_employee'),
            'description': 'Select either an in-house employee OR a remote employee (not both).'
        }),
        ('Request Details', {
            'fields': ('request_date', 'leaving_time', 'return_time', 'destination', 'customer_name', 'reason')
        }),
        ('Status', {
            'fields': ('status', 'admin_notes', 'created_at', 'reviewed_at')
        }),
    )
    
    def get_employee_name(self, obj):
        return obj.employee.name if obj.employee else obj.remote_employee.name
    get_employee_name.short_description = 'Employee'
    get_employee_name.admin_order_field = 'employee__name'


# ============================================
# Leave Request Admin
# ============================================

@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    list_display = ('employee', 'leave_type', 'start_date', 'end_date', 'requested_days', 'approved_days', 'status', 'created_at')
    list_filter = ('status', 'leave_type', 'created_at')
    search_fields = ('employee__name', 'employee__person_id', 'reason')
    ordering = ('-created_at',)
    date_hierarchy = 'start_date'
    readonly_fields = ('created_at', 'reviewed_at', 'requested_days')
    
    fieldsets = (
        ('Employee', {
            'fields': ('employee',)
        }),
        ('Leave Details', {
            'fields': ('leave_type', 'start_date', 'end_date', 'requested_days', 'reason', 'document')
        }),
        ('Approval', {
            'fields': ('status', 'approved_days', 'admin_notes', 'reviewed_at')
        }),
        ('Timestamps', {
            'fields': ('created_at',),
            'classes': ('collapse',)
        }),
    )


# ============================================
# Phase 2 — Master Data / Lookup Tables
# ============================================

@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active', 'team_count', 'designation_count', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('name', 'description')
    ordering = ('name',)
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        (None, {
            'fields': ('name', 'description', 'is_active')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def team_count(self, obj):
        return obj.teams.count()
    team_count.short_description = 'Teams'

    def designation_count(self, obj):
        return obj.designations.count()
    designation_count.short_description = 'Designations'


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ('name', 'department', 'is_active', 'created_at')
    list_filter = ('is_active', 'department')
    search_fields = ('name', 'description')
    ordering = ('name',)
    readonly_fields = ('created_at', 'updated_at')
    autocomplete_fields = ('department',)
    fieldsets = (
        (None, {
            'fields': ('name', 'department', 'description', 'is_active')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('name', 'description')
    ordering = ('name',)
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        (None, {
            'fields': ('name', 'description', 'is_active')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(EmploymentHistory)
class EmploymentHistoryAdmin(admin.ModelAdmin):
    list_display = ('employee', 'change_type', 'effective_date', 'previous_value', 'new_value', 'changed_by', 'changed_at')
    list_filter = ('change_type', 'effective_date')
    search_fields = ('employee__name', 'employee__person_id', 'changed_by', 'reason')
    ordering = ('-effective_date', '-changed_at')
    date_hierarchy = 'effective_date'
    readonly_fields = ('employee', 'change_type', 'effective_date',
                       'previous_value', 'new_value', 'reason',
                       'changed_by', 'changed_at')

    def has_add_permission(self, request):
        return False   # history is auto-created only

    def has_change_permission(self, request, obj=None):
        return False   # immutable log — no editing via admin

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser  # superuser can purge if needed


@admin.register(SalaryStructure)
class SalaryStructureAdmin(admin.ModelAdmin):
    list_display = (
        'employee', 'effective_from', 'currency',
        'basic', 'housing', 'transport', 'phone', 'other_allowance',
        'gross_display', 'status', 'created_by', 'created_at',
    )
    list_filter = ('status', 'currency', 'effective_from')
    search_fields = ('employee__name', 'employee__person_id', 'created_by', 'revision_reason')
    ordering = ('-effective_from', '-created_at')
    date_hierarchy = 'effective_from'
    readonly_fields = (
        'employee', 'effective_from', 'currency',
        'basic', 'housing', 'transport', 'phone', 'other_allowance',
        'revision_reason', 'status', 'created_by', 'created_at',
    )

    def gross_display(self, obj):
        return f"{obj.currency} {obj.gross:,.2f}"
    gross_display.short_description = 'Gross'

    def has_add_permission(self, request):
        return False   # revisions are created only through the employee profile view

    def has_change_permission(self, request, obj=None):
        return False   # immutable — no editing via admin

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser  # superuser can purge if needed


@admin.register(EmployerCostSetup)
class EmployerCostSetupAdmin(admin.ModelAdmin):
    list_display = (
        'get_employee_name', 'effective_from',
        'manpower_monthly_fee', 'medical_insurance_monthly', 'eos_provision_monthly',
        'total_monthly_cost_display', 'created_by', 'created_at',
    )
    list_filter = ('effective_from',)
    search_fields = (
        'employee__name', 'employee__person_id',
        'remote_employee__name', 'remote_employee__extension_id',
        'created_by',
    )
    ordering = ('-effective_from', '-created_at')
    date_hierarchy = 'effective_from'
    readonly_fields = (
        'employee', 'remote_employee', 'effective_from',
        'manpower_monthly_fee', 'visa_amortisation_monthly',
        'visa_status_change_amortisation', 'medical_insurance_monthly',
        'eos_provision_monthly', 'leave_salary_provision_monthly',
        'air_ticket_provision_monthly', 'recruitment_cost_allocation',
        'other_cost_monthly', 'notes', 'created_by', 'created_at',
    )

    def get_employee_name(self, obj):
        if obj.employee:
            return obj.employee.name
        if obj.remote_employee:
            return obj.remote_employee.name
        return '—'
    get_employee_name.short_description = 'Employee'
    get_employee_name.admin_order_field = 'employee__name'

    def total_monthly_cost_display(self, obj):
        return f"{obj.total_monthly_cost:,.2f}"
    total_monthly_cost_display.short_description = 'Total Monthly Cost'

    def has_add_permission(self, request):
        return False   # records created through employee profile view only

    def has_change_permission(self, request, obj=None):
        return False   # immutable — no editing via admin

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser   # superuser can purge if needed


@admin.register(EmployeeDocument)
class EmployeeDocumentAdmin(admin.ModelAdmin):
    list_display = (
        'get_employee_name', 'document_type', 'document_number',
        'issue_date', 'expiry_date', 'expiry_status_display',
        'is_verified', 'created_by', 'created_at',
    )
    list_filter = ('document_type', 'is_verified', 'expiry_date')
    search_fields = (
        'employee__name', 'employee__person_id',
        'remote_employee__name', 'remote_employee__extension_id',
        'document_number', 'created_by',
    )
    ordering = ('document_type', '-created_at')
    date_hierarchy = 'created_at'
    readonly_fields = ('created_by', 'created_at')

    fieldsets = (
        ('Employee', {
            'fields': ('employee', 'remote_employee'),
            'description': 'Select either an in-house or a remote employee (not both).',
        }),
        ('Document Details', {
            'fields': ('document_type', 'document_number', 'issuing_country', 'issue_date', 'expiry_date', 'file'),
        }),
        ('Verification', {
            'fields': ('is_verified', 'verified_by', 'verified_at'),
        }),
        ('Notes & Audit', {
            'fields': ('notes', 'created_by', 'created_at'),
            'classes': ('collapse',),
        }),
    )

    def get_employee_name(self, obj):
        if obj.employee:
            return obj.employee.name
        if obj.remote_employee:
            return obj.remote_employee.name
        return '—'
    get_employee_name.short_description = 'Employee'
    get_employee_name.admin_order_field = 'employee__name'

    def expiry_status_display(self, obj):
        status_map = {
            'expired':       '🔴 Expired',
            'expiring_soon': '🟡 Expiring Soon',
            'valid':         '🟢 Valid',
            'no_expiry':     '— No Expiry',
        }
        return status_map.get(obj.expiry_status, '—')
    expiry_status_display.short_description = 'Expiry Status'


@admin.register(Recoverable)
class RecoverableAdmin(admin.ModelAdmin):
    list_display = (
        'get_employee_name', 'recoverable_type', 'description',
        'currency', 'total_amount', 'amount_recovered', 'outstanding_balance_display',
        'status', 'recovery_start_display', 'created_by', 'created_at',
    )
    list_filter = ('recoverable_type', 'status', 'currency')
    search_fields = (
        'employee__name', 'employee__person_id',
        'remote_employee__name', 'remote_employee__extension_id',
        'description', 'created_by',
    )
    ordering = ('-created_at',)
    date_hierarchy = 'created_at'
    readonly_fields = ('created_by', 'created_at', 'outstanding_balance_display')

    fieldsets = (
        ('Employee', {
            'fields': ('employee', 'remote_employee'),
            'description': 'Select either an in-house or a remote employee (not both).',
        }),
        ('Recoverable Details', {
            'fields': ('recoverable_type', 'description', 'total_amount', 'currency'),
        }),
        ('Recovery Schedule', {
            'fields': ('monthly_recovery', 'recovery_start_year', 'recovery_start_month'),
        }),
        ('Recovery Progress', {
            'fields': ('amount_recovered', 'outstanding_balance_display', 'status'),
        }),
        ('Waiver', {
            'fields': ('waived_by', 'waived_at', 'waived_reason'),
            'classes': ('collapse',),
            'description': 'Complete only when status is set to Waived.',
        }),
        ('Notes & Audit', {
            'fields': ('notes', 'created_by', 'created_at'),
            'classes': ('collapse',),
        }),
    )

    def get_employee_name(self, obj):
        if obj.employee:
            return obj.employee.name
        if obj.remote_employee:
            return obj.remote_employee.name
        return '—'
    get_employee_name.short_description = 'Employee'
    get_employee_name.admin_order_field = 'employee__name'

    def outstanding_balance_display(self, obj):
        bal = obj.outstanding_balance
        return f'{obj.currency} {bal:,.2f}'
    outstanding_balance_display.short_description = 'Outstanding'

    def recovery_start_display(self, obj):
        return f'{obj.recovery_start_month:02d}/{obj.recovery_start_year}'
    recovery_start_display.short_description = 'Starts'

    # Phase 13 — audit trail. Recoverable status changes (settle/waive/adjust
    # monthly_recovery) happen ONLY through this admin today (per Phase 8
    # design), so this is the single real capture point for those edits.
    def save_model(self, request, obj, form, change):
        if not change and not obj.created_by:
            obj.created_by = request.user.username
        before = {}
        if change:
            try:
                prior = Recoverable.objects.get(pk=obj.pk)
                before = {f: getattr(prior, f) for f in
                         ('status', 'monthly_recovery', 'amount_recovered', 'waived_reason')}
            except Recoverable.DoesNotExist:
                before = {}
        super().save_model(request, obj, form, change)
        if change:
            from .audit import diff_fields
            after = {f: getattr(obj, f) for f in
                     ('status', 'monthly_recovery', 'amount_recovered', 'waived_reason')}
            changes = diff_fields(before, after)
            if changes:
                log_audit(actor=request.user.username, action=AuditLog.ACTION_UPDATE,
                          instance=obj, changes=changes, note='Edited via Django admin')
        else:
            log_audit(actor=request.user.username, action=AuditLog.ACTION_CREATE,
                      instance=obj, note='Created via Django admin')

    def delete_model(self, request, obj):
        log_audit(actor=request.user.username, action=AuditLog.ACTION_DELETE,
                  instance=obj, note='Deleted via Django admin')
        super().delete_model(request, obj)


@admin.register(DesignationMaster)
class DesignationMasterAdmin(admin.ModelAdmin):
    list_display = ('title', 'department', 'is_active', 'created_at')
    list_filter = ('is_active', 'department')
    search_fields = ('title', 'description')
    ordering = ('title',)
    readonly_fields = ('created_at', 'updated_at')
    autocomplete_fields = ('department',)
    fieldsets = (
        (None, {
            'fields': ('title', 'department', 'description', 'is_active')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """
    Read-only admin view of the audit trail — a secondary way to browse
    entries alongside the dedicated /payroll/audit-log/ page. No add/edit/
    delete: the audit trail must stay append-only and tamper-evident.
    """
    list_display = ('timestamp', 'actor', 'action', 'model_name', 'object_id', 'object_repr', 'note')
    list_filter = ('action', 'app_label', 'model_name')
    search_fields = ('actor', 'object_repr', 'object_id', 'note')
    date_hierarchy = 'timestamp'
    ordering = ('-timestamp',)
    readonly_fields = ('timestamp', 'actor', 'action', 'app_label', 'model_name',
                       'object_id', 'object_repr', 'changes', 'note')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
