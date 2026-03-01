"""URL configuration for /api/v1/admin/roles/ endpoints."""
from django.urls import path

from apps.rbac.views import (
    FeaturesView,
    RoleCreateView,
    RoleDeleteView,
    RoleDetailView,
    RoleListView,
    RolePermissionsUpdateView,
    RoleUpdateView,
)

urlpatterns = [
    path('', RoleListView.as_view(), name='admin-role-list'),
    path('create/', RoleCreateView.as_view(), name='admin-role-create'),
    path('<uuid:pk>/', RoleDetailView.as_view(), name='admin-role-detail'),
    path('<uuid:pk>/update/', RoleUpdateView.as_view(), name='admin-role-update'),
    path('<uuid:pk>/delete/', RoleDeleteView.as_view(), name='admin-role-delete'),
    path('<uuid:pk>/permissions/', RolePermissionsUpdateView.as_view(), name='admin-role-permissions'),
]
