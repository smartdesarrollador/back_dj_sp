"""URL configuration for /api/v1/admin/users/ endpoints."""
from django.urls import path

from apps.auth_app.admin_views import (
    UserCreateView,
    UserDetailView,
    UserInviteView,
    UserListView,
    UserRoleAssignView,
    UserRoleRemoveView,
    UserSuspendView,
    UserUpdateView,
)

urlpatterns = [
    path('', UserListView.as_view(), name='admin-user-list'),
    path('create/', UserCreateView.as_view(), name='admin-user-create'),
    path('invite/', UserInviteView.as_view(), name='admin-user-invite'),
    path('<uuid:pk>/', UserDetailView.as_view(), name='admin-user-detail'),
    path('<uuid:pk>/update/', UserUpdateView.as_view(), name='admin-user-update'),
    path('<uuid:pk>/suspend/', UserSuspendView.as_view(), name='admin-user-suspend'),
    path('<uuid:pk>/roles/', UserRoleAssignView.as_view(), name='admin-user-role-assign'),
    path('<uuid:pk>/roles/<uuid:role_pk>/', UserRoleRemoveView.as_view(), name='admin-user-role-remove'),
]
