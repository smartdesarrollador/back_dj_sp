from django.urls import path

from apps.analytics.admin_views import (
    AdminSummaryView,
    DesktopLicenseFunnelView,
    ServiceAdoptionView,
    StorageReportView,
    VistaTrafficView,
)

urlpatterns = [
    path('summary/', AdminSummaryView.as_view(), name='admin-report-summary'),
    path('service-adoption/', ServiceAdoptionView.as_view(), name='admin-report-service-adoption'),
    path('storage/', StorageReportView.as_view(), name='admin-report-storage'),
    path('vista-traffic/', VistaTrafficView.as_view(), name='admin-report-vista-traffic'),
    path('desktop-licenses/', DesktopLicenseFunnelView.as_view(), name='admin-report-desktop-licenses'),
]
