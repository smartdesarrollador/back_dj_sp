from django.urls import path

from apps.licenses.views import MyLicenseView, RequestLicenseView, ResendLicenseView

urlpatterns = [
    path('', MyLicenseView.as_view(), name='app-my-license'),
    path('request/', RequestLicenseView.as_view(), name='app-license-request'),
    path('resend/', ResendLicenseView.as_view(), name='app-license-resend'),
]
