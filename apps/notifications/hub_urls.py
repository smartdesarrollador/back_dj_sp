from django.urls import path

from apps.notifications.views import (
    AdminNotificationMarkAllReadView,
    AdminNotificationMarkReadView,
    HubNotificationListView,
)

urlpatterns = [
    path('', HubNotificationListView.as_view(), name='hub-notification-list'),
    path('read-all/', AdminNotificationMarkAllReadView.as_view(), name='hub-notification-read-all'),
    path('<uuid:pk>/read/', AdminNotificationMarkReadView.as_view(), name='hub-notification-mark-read'),
]
