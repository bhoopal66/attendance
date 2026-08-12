from django.contrib import admin
from .models import PayrollAdjustment, Bank, BankSubmission, ExchangeRate, CommissionTierSettings


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
    list_display = ('name', 'per_account_charge', 'inr_per_account_charge', 'npr_per_account_charge', 'is_active', 'created_at')
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
