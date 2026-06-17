from django.urls import path

from apps.subscriptions.views import (
    CancelSubscriptionView,
    CurrentSubscriptionView,
    UpgradeSubscriptionView,
)
from apps.subscriptions.yape_upgrade_views import YapeUpgradeView

urlpatterns = [
    path('current', CurrentSubscriptionView.as_view(), name='subscription-current'),
    path('upgrade', UpgradeSubscriptionView.as_view(), name='subscription-upgrade'),
    path('cancel', CancelSubscriptionView.as_view(), name='subscription-cancel'),
    path('yape-upgrade', YapeUpgradeView.as_view(), name='subscription-yape-upgrade'),
]
