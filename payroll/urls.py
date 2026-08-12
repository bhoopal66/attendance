from django.urls import path
from . import views

urlpatterns = [
    path('', views.payroll_test_dashboard, name='payroll_dashboard'),
    path('old/', views.payroll_dashboard, name='payroll_dashboard_old'),
    # Bank management
    path('banks/', views.manage_banks, name='manage_banks'),
    path('api/banks/', views.banks_api, name='banks_api'),
    path('api/banks/<int:bank_id>/', views.bank_detail_api, name='bank_detail_api'),
    path('api/commission-tier/save/', views.save_commission_tier, name='save_commission_tier'),
    # Bank submissions
    path('api/submissions/<str:emp_type>/<int:employee_id>/', views.get_submissions, name='get_submissions'),
    path('api/submissions/save/', views.save_submissions, name='save_submissions'),
    # In-house employee adjustments
    path('api/adjustments/<int:employee_id>/', views.get_adjustments, name='get_adjustments'),
    path('api/adjustments/add/', views.add_adjustment, name='add_adjustment'),
    # Remote employee adjustments
    path('api/remote-adjustments/<int:employee_id>/', views.get_remote_adjustments, name='get_remote_adjustments'),
    path('api/remote-adjustments/add/', views.add_remote_adjustment, name='add_remote_adjustment'),
    # Delete (handles both types)
    path('api/adjustments/delete/<int:adjustment_id>/', views.delete_adjustment, name='delete_adjustment'),
    # Recalculate summaries
    path('api/recalculate/', views.recalculate_summaries, name='recalculate_summaries'),
    # Upload submission XLSX
    path('api/upload-submissions/', views.upload_submissions, name='upload_submissions'),
    # Payroll employee database
    path('employees/', views.payroll_employees, name='payroll_employees'),
    path('api/employee/<str:emp_type>/<int:employee_id>/update/', views.payroll_employee_update, name='payroll_employee_update'),
    # Deductions & Additions
    path('api/deductions/add/', views.add_deduction, name='add_deduction'),
    path('api/deductions/delete/<int:deduction_id>/', views.delete_deduction_entry, name='delete_deduction_entry'),
    path('api/deductions/autofill/', views.autofill_deduction, name='autofill_deduction'),
    # Carryover skip/unskip
    path('api/carryover/<int:carryover_id>/toggle-skip/', views.toggle_carryover_skip, name='toggle_carryover_skip'),
    # Payslip download
    path('payslip/<str:emp_type>/<int:emp_id>/', views.download_payslip, name='download_payslip'),
    # Payslip history (searchable archive of Mark-as-Paid records)
    path('payslip-history/', views.payslip_history, name='payslip_history'),
    # Advance payment voucher
    path('voucher/advance/', views.advance_voucher_download, name='advance_voucher'),
    # Exchange rate
    path('api/exchange-rate/save/', views.save_exchange_rate, name='save_exchange_rate'),
    # Freeze / unfreeze payroll month
    path('api/freeze/', views.freeze_payroll, name='freeze_payroll'),
    path('api/unfreeze/', views.unfreeze_payroll, name='unfreeze_payroll'),
    # Legacy test URL — redirect to main for backward-compat
    path('test/', views.payroll_test_dashboard, name='payroll_test_dashboard'),
    # Mark as Paid / Unmark
    path('api/mark-paid/', views.mark_paid_salary, name='mark_paid_salary'),
    path('api/unmark-paid/', views.unmark_paid_salary, name='unmark_paid_salary'),
]
