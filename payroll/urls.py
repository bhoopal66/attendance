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
]
