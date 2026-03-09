from django.urls import path

from .sso_views import SSOTokenView, SSOValidateView

urlpatterns = [
    path('token/', SSOTokenView.as_view(), name='sso-token'),
    path('validate/', SSOValidateView.as_view(), name='sso-validate'),
]
