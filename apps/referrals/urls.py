from django.urls import path

from .views import ReferralView

urlpatterns = [
    path('', ReferralView.as_view(), name='referral-dashboard'),
]
