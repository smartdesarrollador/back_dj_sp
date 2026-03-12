"""URL aliases for /api/v1/app/team/ — reuses existing admin views."""
from django.urls import path

from apps.auth_app.admin_views import (
    UserInviteView,
    UserListView,
    UserSuspendView,
)

urlpatterns = [
    path('', UserListView.as_view(), name='hub-team-list'),
    path('invite/', UserInviteView.as_view(), name='hub-team-invite'),
    path('<uuid:pk>/suspend/', UserSuspendView.as_view(), name='hub-team-suspend'),
]
