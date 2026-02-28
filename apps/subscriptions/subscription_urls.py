from django.urls import path

from apps.subscriptions.views import (
    CancelSubscriptionView,
    CurrentSubscriptionView,
    UpgradeSubscriptionView,
)

urlpatterns = [
    path('current', CurrentSubscriptionView.as_view(), name='subscription-current'),
    path('upgrade', UpgradeSubscriptionView.as_view(), name='subscription-upgrade'),
    path('cancel', CancelSubscriptionView.as_view(), name='subscription-cancel'),
]
