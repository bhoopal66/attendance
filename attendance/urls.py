from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

urlpatterns = [
    path('', views.upload_file, name='upload'),
    path('upload/remote/', views.upload_remote_call_stats, name='upload_remote'),
    path('report/', views.attendance_report, name='report'),
    path('report/download/', views.download_report, name='download_report'),
    path('report/download/employee/<int:employee_id>/', views.download_employee_report, name='download_employee_report'),
    path('report/remote/', views.remote_attendance_report, name='remote_report'),
    path('report/remote/download/', views.download_remote_report, name='download_remote_report'),
    path('report/remote/download/employee/<int:employee_id>/', views.download_remote_employee_report, name='download_remote_employee_report'),
    path('api/attendance/update/', views.update_attendance, name='update_attendance'),
    path('login/', auth_views.LoginView.as_view(redirect_authenticated_user=True), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    # Employee Portal
    path('portal/', views.employee_portal, name='employee_portal'),
    path('portal/login/', views.employee_login, name='employee_login'),
    path('portal/logout/', views.employee_logout, name='employee_logout'),
    path('portal/early-leave-request/', views.submit_early_leave_request, name='submit_early_leave'),
    path('portal/leave-request/', views.submit_leave_request, name='submit_leave_request'),
    path('portal/api/my-requests/', views.get_my_requests, name='get_my_requests'),
    path('portal/change-password/', views.employee_change_password, name='employee_change_password'),
    # Request Approval Endpoints (Admin)
    path('request/<int:request_id>/data/', views.get_request_attendance_data, name='get_request_data'),
    path('request/<int:request_id>/approve/', views.approve_early_leave, name='approve_request'),
    path('request/<int:request_id>/decline/', views.decline_early_leave, name='decline_request'),
    path('api/pending-count/', views.get_pending_count, name='get_pending_count'),
    path('api/pending-requests/', views.get_pending_requests, name='get_pending_requests'),
    # Employee Management (Admin)
    path('employees/', views.employee_management, name='employee_management'),
    path('employees/update/', views.update_employee, name='update_employee'),
    path('employees/bulk-update/', views.bulk_update_employees, name='bulk_update_employees'),
    path('employees/merge/', views.merge_employees, name='merge_employees'),
    path('employees/delete/', views.delete_employee, name='delete_employee'),
    path('employees/link/', views.link_employees, name='link_employees'),
    path('employees/unlink/', views.unlink_employees, name='unlink_employees'),
    # Leave Management (Admin)
    path('leave-requests/', views.leave_management, name='leave_management'),
    path('leave/<int:leave_id>/approve/', views.approve_leave_request, name='approve_leave'),
    path('leave/<int:leave_id>/reject/', views.reject_leave_request, name='reject_leave'),
    # Special Shift Periods (Admin)
    path('special-shifts/', views.special_shift_periods, name='special_shift_periods'),
    path('special-shifts/add/', views.add_special_shift_period, name='add_special_shift_period'),
    path('special-shifts/<int:period_id>/update/', views.update_special_shift_period, name='update_special_shift_period'),
    path('special-shifts/<int:period_id>/delete/', views.delete_special_shift_period, name='delete_special_shift_period'),
]

