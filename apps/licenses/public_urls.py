from django.urls import path

from apps.licenses.views import ActivateLicenseView

urlpatterns = [
    path('activate/', ActivateLicenseView.as_view(), name='public-license-activate'),
]
