"""URL routing for workspace export endpoints (/api/v1/app/workspace/)."""
from django.urls import path

from apps.exports.views import WorkspaceBackupView

urlpatterns = [
    path('backup/', WorkspaceBackupView.as_view(), name='workspace-backup'),
]
