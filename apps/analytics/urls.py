from django.urls import path

from apps.analytics.views import (
    ActivityView,
    DevOpsView,
    ReportExportView,
    SummaryView,
    TrendsView,
    UsageView,
)

urlpatterns = [
    path('summary/', SummaryView.as_view(), name='report-summary'),
    path('usage/', UsageView.as_view(), name='report-usage'),
    path('devops/', DevOpsView.as_view(), name='report-devops'),
    path('activity/', ActivityView.as_view(), name='report-activity'),
    path('trends/', TrendsView.as_view(), name='report-trends'),
    path('export/', ReportExportView.as_view(), name='report-export'),
]
