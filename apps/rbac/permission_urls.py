"""URL configuration for /api/v1/admin/permissions/ endpoint."""
from django.urls import path

from apps.rbac.views import PermissionListView

urlpatterns = [
    path('', PermissionListView.as_view(), name='admin-permission-list'),
]
