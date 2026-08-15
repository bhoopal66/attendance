from django.urls import path
from . import views
from . import views_payroll_run
from . import views_performance
from . import views_profitability
from . import views_management
from . import views_audit
from . import views_notes
from . import views_range_report
from . import views_deduction_types
from . import views_loans
from . import views_rules
from . import views_debug

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
    path('api/add-payment/', views.add_partial_payment, name='add_partial_payment'),

    # Phase 9 — Payroll Run lifecycle
    path('run/<int:year>/<int:month>/', views_payroll_run.payroll_run_detail, name='payroll_run_detail'),
    # Phase 10 — Team Performance
    path('performance/<int:year>/<int:month>/', views_performance.team_performance, name='payroll_team_performance'),
    # Phase 11 — Profitability
    path('profitability/<int:year>/<int:month>/', views_profitability.profitability, name='payroll_profitability'),
    # Phase 12 — Management Dashboard
    path('management/', views_management.management_home, name='payroll_management_home'),
    path('management/<int:year>/<int:month>/', views_management.management_dashboard, name='payroll_management'),
    # Phase 13 — Audit Log
    path('audit-log/', views_audit.audit_log, name='payroll_audit_log'),

    # Phase C — Per-employee Notes & Timeline
    path('api/notes/<str:emp_type>/<int:employee_id>/', views_notes.get_employee_notes, name='get_employee_notes'),
    path('api/notes/add/', views_notes.add_employee_note, name='add_employee_note'),

    # Phase 2 — Deduction Master (configurable deduction/addition types)
    path('deduction-types/', views_deduction_types.deduction_types, name='deduction_types'),
    path('api/deduction-types/save/', views_deduction_types.deduction_type_save, name='deduction_type_save'),
    path('api/deduction-types/toggle/', views_deduction_types.deduction_type_toggle, name='deduction_type_toggle'),
    path('api/deduction-types/delete/', views_deduction_types.deduction_type_delete, name='deduction_type_delete'),

    # Phase 3 — Loans & Salary Advances
    path('loans/', views_loans.loans, name='loans'),
    path('api/loans/preview/', views_loans.loan_preview, name='loan_preview'),
    path('api/loans/save/', views_loans.loan_save, name='loan_save'),
    path('api/loans/activate/', views_loans.loan_activate, name='loan_activate'),
    path('api/loans/cancel/', views_loans.loan_cancel, name='loan_cancel'),
    path('api/loans/waive/', views_loans.loan_waive, name='loan_waive'),
    path('api/loans/delete/', views_loans.loan_delete, name='loan_delete'),

    # Phase 4 — Deduction rules & limits
    path('deduction-limits/', views_rules.deduction_rules, name='deduction_rules'),
    path('api/rules/save/', views_rules.rule_save, name='rule_save'),
    path('api/rules/toggle/', views_rules.rule_toggle, name='rule_toggle'),
    path('api/rules/delete/', views_rules.rule_delete, name='rule_delete'),

    # Phase D — Range / Annual Report
    path('range-report/', views_range_report.range_report, name='payroll_range_report'),

    # TEMPORARY — Phase D investigation diagnostic (read-only). Remove after use.
    path('api/debug/snapshot/', views_debug.inspect_snapshot, name='debug_snapshot'),
]
