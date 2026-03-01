from django.urls import path

from apps.analytics.views import ReportExportView, SummaryView, TrendsView, UsageView

urlpatterns = [
    path('summary/', SummaryView.as_view(), name='report-summary'),
    path('usage/', UsageView.as_view(), name='report-usage'),
    path('trends/', TrendsView.as_view(), name='report-trends'),
    path('export/', ReportExportView.as_view(), name='report-export'),
]
