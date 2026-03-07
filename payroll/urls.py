from django.urls import path
from . import views

urlpatterns = [
    path('', views.payroll_dashboard, name='payroll_dashboard'),
    # In-house employee adjustments
    path('api/adjustments/<int:employee_id>/', views.get_adjustments, name='get_adjustments'),
    path('api/adjustments/add/', views.add_adjustment, name='add_adjustment'),
    # Remote employee adjustments
    path('api/remote-adjustments/<int:employee_id>/', views.get_remote_adjustments, name='get_remote_adjustments'),
    path('api/remote-adjustments/add/', views.add_remote_adjustment, name='add_remote_adjustment'),
    # Delete (handles both types)
    path('api/adjustments/delete/<int:adjustment_id>/', views.delete_adjustment, name='delete_adjustment'),
]
