from django.contrib import admin
from .models import PayrollAdjustment


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
