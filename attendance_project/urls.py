from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

# Customize Django Admin
admin.site.site_header = "TCR Attendance Admin"
admin.site.site_title = "TCR Attendance"
admin.site.index_title = "Administration"

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('attendance.urls')),
    path('payroll/', include('payroll.urls')),
]

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
