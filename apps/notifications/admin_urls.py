from django.urls import path

from apps.notifications.views import (
    AdminNotificationListView,
    AdminNotificationMarkAllReadView,
    AdminNotificationMarkReadView,
)

urlpatterns = [
    path('', AdminNotificationListView.as_view(), name='admin-notification-list'),
    path('read-all/', AdminNotificationMarkAllReadView.as_view(), name='admin-notification-read-all'),
    path('<uuid:pk>/read/', AdminNotificationMarkReadView.as_view(), name='admin-notification-mark-read'),
]
