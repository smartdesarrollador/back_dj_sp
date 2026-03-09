from django.urls import include, path

from .views import (
    ForgotPasswordView,
    LoginView,
    LogoutView,
    MFADisableView,
    MFAEnableView,
    MFARecoveryView,
    MFAValidateView,
    MFAVerifySetupView,
    RefreshTokenView,
    RegisterView,
    ResetPasswordView,
    VerifyEmailView,
)

urlpatterns = [
    path('register', RegisterView.as_view(), name='auth-register'),
    path('login', LoginView.as_view(), name='auth-login'),
    path('refresh-token', RefreshTokenView.as_view(), name='auth-refresh-token'),
    path('logout', LogoutView.as_view(), name='auth-logout'),
    path('verify-email', VerifyEmailView.as_view(), name='auth-verify-email'),
    path('forgot-password', ForgotPasswordView.as_view(), name='auth-forgot-password'),
    path('reset-password', ResetPasswordView.as_view(), name='auth-reset-password'),
    path('mfa/enable', MFAEnableView.as_view(), name='mfa-enable'),
    path('mfa/verify-setup', MFAVerifySetupView.as_view(), name='mfa-verify-setup'),
    path('mfa/validate', MFAValidateView.as_view(), name='mfa-validate'),
    path('mfa/disable', MFADisableView.as_view(), name='mfa-disable'),
    path('mfa/recovery', MFARecoveryView.as_view(), name='mfa-recovery'),
    path('sso/', include('apps.auth_app.sso_urls')),
]
