from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from .models import Employee, AttendanceRecord, MonthlySummary, ShiftHistory, RemoteEmployee, RemoteCallRecord, RemoteMonthlySummary, Holiday, EarlyLeaveRequest, LeaveRequest, UserProfile


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
    list_display = ('person_id', 'name', 'is_active', 'email', 'shift_start', 'shift_end')
    list_filter = ('is_active', 'department', 'location', 'team')
    search_fields = ('person_id', 'name', 'email')
    ordering = ('name',)
    inlines = [ShiftHistoryInline]
    fieldsets = (
        (None, {
            'fields': ('person_id', 'name')
        }),
        ('Employment Status', {
            'fields': ('is_active', 'leaving_date'),
            'description': 'Mark as inactive when employee leaves. Their historical records will be preserved.'
        }),
        ('Current Shift (used as fallback if no history)', {
            'fields': ('shift_start', 'shift_end'),
            'description': 'These are used only if no Shift History entries exist for this employee.'
        }),
        ('Portal Login', {
            'fields': ('email', 'portal_password'),
            'description': 'Set email and password for employee self-service portal access.'
        }),
        ('Organization', {
            'fields': ('department', 'location', 'team'),
        }),
        ('Additional Information', {
            'fields': ('salary', 'joining_date', 'designation', 'phone'),
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

