from django.urls import path

from apps.analytics.admin_views import (
    AdminSummaryView,
    DesktopLicenseFunnelView,
    ServiceAdoptionView,
    VistaTrafficView,
)

urlpatterns = [
    path('summary/', AdminSummaryView.as_view(), name='admin-report-summary'),
    path('service-adoption/', ServiceAdoptionView.as_view(), name='admin-report-service-adoption'),
    path('vista-traffic/', VistaTrafficView.as_view(), name='admin-report-vista-traffic'),
    path('desktop-licenses/', DesktopLicenseFunnelView.as_view(), name='admin-report-desktop-licenses'),
]
