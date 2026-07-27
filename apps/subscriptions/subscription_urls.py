from django.urls import path

from apps.subscriptions.views import (
    CancelSubscriptionView,
    CurrentSubscriptionView,
    StartTrialView,
    UpgradeSubscriptionView,
)
from apps.subscriptions.plan_upgrade_views import PlanUpgradeView

urlpatterns = [
    path('current/', CurrentSubscriptionView.as_view(), name='subscription-current'),
    path('upgrade/', UpgradeSubscriptionView.as_view(), name='subscription-upgrade'),
    path('cancel/', CancelSubscriptionView.as_view(), name='subscription-cancel'),
    path('plan-upgrade/', PlanUpgradeView.as_view(), name='subscription-plan-upgrade'),
    path('trial/', StartTrialView.as_view(), name='subscription-trial'),
]
