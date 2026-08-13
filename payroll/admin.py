from django.contrib import admin
from django.utils.html import format_html

from .models import (
    PayrollAdjustment, Bank, BankSubmission, ExchangeRate, CommissionTierSettings,
    # Phase 9 — Payroll Lifecycle
    PayrollRun,
    # Phase 10 — Targets & Revenue
    EmployeeTarget,
)


@admin.register(PayrollAdjustment)
class PayrollAdjustmentAdmin(admin.ModelAdmin):
    list_display = ('get_employee_name', 'get_employee_type', 'year', 'month', 'adjustment_type', 'amount', 'reason', 'created_at')
    list_filter = ('adjustment_type', 'year', 'month')
    search_fields = ('employee__name', 'remote_employee__name', 'reason')
    ordering = ('-year', '-month', '-created_at')

    def get_employee_name(self, obj):
        return obj.employee.name if obj.employee else obj.remote_employee.name
    get_employee_name.short_description = 'Employee'

    def get_employee_type(self, obj):
        return 'In-house' if obj.employee else 'Remote'
    get_employee_type.short_description = 'Type'


@admin.register(Bank)
class BankAdmin(admin.ModelAdmin):
    # Phase 10: revenue_per_account added
    list_display = ('name', 'per_account_charge', 'inr_per_account_charge', 'npr_per_account_charge', 'revenue_per_account', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('name',)


@admin.register(BankSubmission)
class BankSubmissionAdmin(admin.ModelAdmin):
    list_display = ('get_employee_name', 'bank', 'year', 'month', 'submission_count', 'get_commission')
    list_filter = ('bank', 'year', 'month')
    search_fields = ('employee__name', 'remote_employee__name', 'bank__name')
    ordering = ('-year', '-month')

    def get_employee_name(self, obj):
        return obj.employee.name if obj.employee else obj.remote_employee.name
    get_employee_name.short_description = 'Employee'

    def get_commission(self, obj):
        return f"AED {obj.commission:.2f}"
    get_commission.short_description = 'Commission'


@admin.register(ExchangeRate)
class ExchangeRateAdmin(admin.ModelAdmin):
    list_display = ('currency', 'year', 'month', 'rate', 'updated_at')
    list_filter = ('currency', 'year')
    ordering = ('-year', '-month')


@admin.register(CommissionTierSettings)
class CommissionTierSettingsAdmin(admin.ModelAdmin):
    list_display = ('currency', 'threshold', 'overflow_rate', 'updated_at')


# ─────────────────────────────────────────────────────────────────────────────
# PayrollRun Admin  (Phase 9)
# ─────────────────────────────────────────────────────────────────────────────

@admin.register(PayrollRun)
class PayrollRunAdmin(admin.ModelAdmin):
    """
    Read-mostly admin for PayrollRun lifecycle records.
    Status transitions happen through the dedicated Run Status page;
    the admin is primarily for audit / override purposes.
    """

    list_display = (
        'period_display',
        'status_badge',
        'prepared_by', 'prepared_at',
        'reviewed_by', 'reviewed_at',
        'approved_by', 'approved_at',
        'locked_by',   'locked_at',
        'paid_by',     'paid_at',
        'posted_by',   'posted_at',
    )
    list_display_links = ('period_display',)
    list_filter = ('status', 'year')
    search_fields = (
        'prepared_by', 'reviewed_by', 'approved_by',
        'locked_by', 'paid_by', 'posted_by',
    )
    ordering = ('-year', '-month')

    readonly_fields = (
        'year', 'month', 'status',
        'prepared_by', 'prepared_at',
        'reviewed_by', 'reviewed_at',
        'approved_by', 'approved_at',
        'locked_by',   'locked_at',
        'paid_at',     'paid_by',
        'posted_by',   'posted_at',
        'created_at',  'updated_at',
    )

    fieldsets = (
        ('Period', {
            'fields': ('year', 'month', 'status'),
        }),
        ('Lifecycle Audit Trail', {
            'fields': (
                ('prepared_by',  'prepared_at'),
                ('reviewed_by',  'reviewed_at'),
                ('approved_by',  'approved_at'),
                ('locked_by',    'locked_at'),
                ('paid_by',      'paid_at'),
                ('posted_by',    'posted_at'),
            ),
        }),
        ('Notes', {
            'fields': ('notes',),
        }),
        ('System', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    MONTH_NAMES = [
        '', 'January', 'February', 'March', 'April', 'May', 'June',
        'July', 'August', 'September', 'October', 'November', 'December',
    ]

    STATUS_COLOURS = {
        'draft':    ('#6B7280', '#F3F4F6'),   # grey
        'review':   ('#92400E', '#FEF3C7'),   # amber
        'approved': ('#065F46', '#D1FAE5'),   # green
        'locked':   ('#9A3412', '#FFEDD5'),   # orange
        'paid':     ('#14532D', '#DCFCE7'),   # dark green
        'posted':   ('#1E3A5F', '#E0F2FE'),   # slate
    }

    @admin.display(description='Period', ordering='-year')
    def period_display(self, obj):
        month_str = self.MONTH_NAMES[obj.month] if 1 <= obj.month <= 12 else str(obj.month)
        return f'{month_str} {obj.year}'

    @admin.display(description='Status')
    def status_badge(self, obj):
        colour, bg = self.STATUS_COLOURS.get(obj.status, ('#374151', '#F9FAFB'))
        return format_html(
            '<span style="'
            'display:inline-block;padding:2px 10px;border-radius:12px;'
            'font-size:.75rem;font-weight:600;'
            'color:{};background:{};">{}</span>',
            colour, bg,
            obj.get_status_display(),
        )


# ─────────────────────────────────────────────────────────────────────────────
# EmployeeTarget Admin  (Phase 10)
# ─────────────────────────────────────────────────────────────────────────────

@admin.register(EmployeeTarget)
class EmployeeTargetAdmin(admin.ModelAdmin):
    """
    Admin for monthly funded-account targets.
    Achievement columns are computed live from BankSubmission data.
    """

    list_display = (
        'person_display',
        'kind_display',
        'period_display',
        'target_accounts',
        'achieved_display',
        'pct_display',
        'revenue_display',
        'updated_by',
        'updated_at',
    )
    list_filter = ('year', 'month')
    search_fields = (
        'employee__name', 'employee__person_id',
        'remote_employee__name', 'remote_employee__extension_id',
        'notes',
    )
    ordering = ('-year', '-month')
    raw_id_fields = ('employee', 'remote_employee')
    readonly_fields = ('created_by', 'created_at', 'updated_by', 'updated_at')

    fieldsets = (
        ('Employee (choose exactly one)', {
            'fields': ('employee', 'remote_employee'),
        }),
        ('Target', {
            'fields': ('year', 'month', 'target_accounts', 'notes'),
        }),
        ('Audit', {
            'fields': ('created_by', 'created_at', 'updated_by', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    MONTH_NAMES = [
        '', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
        'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
    ]

    def get_queryset(self, request):
        return (
            super().get_queryset(request)
            .select_related('employee', 'remote_employee')
        )

    @admin.display(description='Employee', ordering='employee__name')
    def person_display(self, obj):
        return obj.person_label

    @admin.display(description='Type')
    def kind_display(self, obj):
        if obj.remote_employee_id:
            return format_html(
                '<span style="background:#FEF3C7;color:#92400E;padding:1px 8px;'
                'border-radius:10px;font-size:.72rem;font-weight:600;">Remote</span>'
            )
        return format_html(
            '<span style="background:#F3F4F6;color:#6B7280;padding:1px 8px;'
            'border-radius:10px;font-size:.72rem;font-weight:600;">In-house</span>'
        )

    @admin.display(description='Period', ordering='-year')
    def period_display(self, obj):
        m = self.MONTH_NAMES[obj.month] if 1 <= obj.month <= 12 else obj.month
        return f'{m} {obj.year}'

    @admin.display(description='Achieved')
    def achieved_display(self, obj):
        return obj.achieved_accounts()

    @admin.display(description='Achievement')
    def pct_display(self, obj):
        pct = obj.achievement_pct()
        if pct is None:
            return '—'
        if pct >= 100:
            colour, bg = '#14532D', '#DCFCE7'
        elif pct >= 70:
            colour, bg = '#92400E', '#FEF3C7'
        else:
            colour, bg = '#991B1B', '#FEE2E2'
        return format_html(
            '<span style="color:{};background:{};padding:2px 10px;'
            'border-radius:12px;font-size:.75rem;font-weight:700;">{}%</span>',
            colour, bg, pct,
        )

    @admin.display(description='Revenue (AED)')
    def revenue_display(self, obj):
        return f'{obj.achieved_revenue():,.2f}'

    def save_model(self, request, obj, form, change):
        if not change and not obj.created_by:
            obj.created_by = request.user.username
        obj.updated_by = request.user.username
        super().save_model(request, obj, form, change)
