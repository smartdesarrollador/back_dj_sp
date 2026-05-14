from django.urls import path

from apps.licenses.views import AdminLicenseDetailView, AdminLicenseListView

urlpatterns = [
    path('', AdminLicenseListView.as_view(), name='admin-license-list'),
    path('<uuid:pk>/', AdminLicenseDetailView.as_view(), name='admin-license-detail'),
]
