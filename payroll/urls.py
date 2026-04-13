from django.urls import path
from . import views

urlpatterns = [
    path('', views.payroll_dashboard, name='payroll_dashboard'),
    # Bank management
    path('banks/', views.manage_banks, name='manage_banks'),
    path('api/banks/', views.banks_api, name='banks_api'),
    path('api/banks/<int:bank_id>/', views.bank_detail_api, name='bank_detail_api'),
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
    # Payslip download
    path('payslip/<str:emp_type>/<int:emp_id>/', views.download_payslip, name='download_payslip'),
    # Advance payment voucher
    path('voucher/advance/', views.advance_voucher_download, name='advance_voucher'),
]
