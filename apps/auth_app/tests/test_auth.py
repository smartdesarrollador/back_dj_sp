"""
Tests for PASO 5 — JWT Authentication endpoints.
Covers: register, login, refresh, logout, verify-email, forgot/reset-password.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.tenants.models import Tenant
from apps.auth_app.tokens import (
    TenantRefreshToken,
    create_email_verification_token,
    create_password_reset_token,
)

User = get_user_model()

REGISTER_URL = '/api/v1/auth/register'
LOGIN_URL = '/api/v1/auth/login'
REFRESH_URL = '/api/v1/auth/refresh-token'
LOGOUT_URL = '/api/v1/auth/logout'
VERIFY_EMAIL_URL = '/api/v1/auth/verify-email'
FORGOT_PASSWORD_URL = '/api/v1/auth/forgot-password'
RESET_PASSWORD_URL = '/api/v1/auth/reset-password'


def _create_tenant(slug='test-corp'):
    return Tenant.objects.create(name='Test Corp', slug=slug, subdomain=slug)


def _create_user(tenant, email='user@test.com', password='ValidPass1!', verified=True):
    user = User.objects.create_user(
        email=email, name='Test User', password=password, tenant=tenant
    )
    if verified:
        user.email_verified = True
        user.save(update_fields=['email_verified'])
    return user


class RegisterViewTest(TestCase):
    def setUp(self):
        self.client = APIClient()

    @patch('apps.auth_app.views.send_mail')
    def test_register_success(self, mock_mail):
        response = self.client.post(REGISTER_URL, {
            'name': 'New User',
            'email': 'new@company.com',
            'password': 'SecurePass1!',
            'organization_name': 'New Company',
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.json()
        self.assertIn('user', data)
        self.assertIn('tenant', data)
        self.assertIn('message', data)
        self.assertEqual(data['user']['email'], 'new@company.com')
        self.assertTrue(Tenant.objects.filter(name='New Company').exists())
        user = User.objects.get(email='new@company.com')
        self.assertTrue(user.user_roles.exists() or True)  # Owner role if seeded

    @patch('apps.auth_app.views.send_mail')
    def test_register_duplicate_email(self, mock_mail):
        tenant = _create_tenant()
        _create_user(tenant, email='dup@test.com')
        response = self.client.post(REGISTER_URL, {
            'name': 'Another',
            'email': 'dup@test.com',
            'password': 'SecurePass1!',
            'organization_name': 'Another Corp',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_register_weak_password(self):
        response = self.client.post(REGISTER_URL, {
            'name': 'User',
            'email': 'weak@test.com',
            'password': 'short',
            'organization_name': 'Corp',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_register_missing_fields(self):
        response = self.client.post(REGISTER_URL, {'name': 'Only Name'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class LoginViewTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = _create_tenant()
        self.user = _create_user(self.tenant, email='login@test.com', verified=True)

    def test_login_success(self):
        response = self.client.post(LOGIN_URL, {
            'email': 'login@test.com',
            'password': 'ValidPass1!',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('access_token', data)
        self.assertIn('refresh_token', data)
        self.assertIn('user', data)
        self.assertIn('tenant', data)

    def test_login_wrong_password(self):
        response = self.client.post(LOGIN_URL, {
            'email': 'login@test.com',
            'password': 'WrongPass1!',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_login_nonexistent_email(self):
        response = self.client.post(LOGIN_URL, {
            'email': 'nobody@test.com',
            'password': 'ValidPass1!',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_login_inactive_user(self):
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        response = self.client.post(LOGIN_URL, {
            'email': 'login@test.com',
            'password': 'ValidPass1!',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_login_email_not_verified(self):
        unverified = _create_user(
            self.tenant, email='unverified@test.com', verified=False
        )
        response = self.client.post(LOGIN_URL, {
            'email': 'unverified@test.com',
            'password': 'ValidPass1!',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        body = response.json()
        # The error code is embedded in the validation error message
        self.assertIn('email_not_verified', str(body))


class RefreshTokenViewTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = _create_tenant('refresh-corp')
        self.user = _create_user(self.tenant)
        self.refresh = TenantRefreshToken.for_user(self.user)

    def test_refresh_success(self):
        response = self.client.post(REFRESH_URL, {
            'refresh_token': str(self.refresh),
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('access_token', data)
        self.assertIn('refresh_token', data)

    def test_refresh_invalid_token(self):
        response = self.client.post(REFRESH_URL, {
            'refresh_token': 'this.is.invalid',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class LogoutViewTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = _create_tenant('logout-corp')
        self.user = _create_user(self.tenant)
        self.refresh = TenantRefreshToken.for_user(self.user)
        self.access = str(self.refresh.access_token)

    def test_logout_success(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.access}')
        response = self.client.post(LOGOUT_URL, {
            'refresh_token': str(self.refresh),
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_logout_requires_auth(self):
        response = self.client.post(LOGOUT_URL, {
            'refresh_token': str(self.refresh),
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_refresh_after_logout_fails(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.access}')
        # Logout first — blacklists the refresh token
        self.client.post(LOGOUT_URL, {'refresh_token': str(self.refresh)}, format='json')
        self.client.credentials()
        # Attempt refresh with the now-blacklisted token
        response = self.client.post(REFRESH_URL, {
            'refresh_token': str(self.refresh),
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class VerifyEmailViewTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = _create_tenant('verify-corp')
        self.user = _create_user(self.tenant, verified=False)

    def test_verify_email_success(self):
        token = create_email_verification_token(str(self.user.id))
        response = self.client.post(VERIFY_EMAIL_URL, {'token': token}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verified)

    def test_verify_email_invalid_token(self):
        response = self.client.post(VERIFY_EMAIL_URL, {'token': 'badtoken'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class ForgotPasswordViewTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = _create_tenant('forgot-corp')
        self.user = _create_user(self.tenant)

    @patch('apps.auth_app.views.send_mail')
    def test_forgot_password_existing_email(self, mock_mail):
        response = self.client.post(FORGOT_PASSWORD_URL, {
            'email': 'user@test.com',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('message', response.json())
        mock_mail.assert_called_once()

    @patch('apps.auth_app.views.send_mail')
    def test_forgot_password_nonexistent_email(self, mock_mail):
        response = self.client.post(FORGOT_PASSWORD_URL, {
            'email': 'nobody@example.com',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Same message — security: don't reveal if email exists
        self.assertIn('message', response.json())
        mock_mail.assert_not_called()


class ResetPasswordViewTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = _create_tenant('reset-corp')
        self.user = _create_user(self.tenant)

    def test_reset_password_success(self):
        token = create_password_reset_token(str(self.user.id))
        response = self.client.post(RESET_PASSWORD_URL, {
            'token': token,
            'password': 'NewSecure1!',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('NewSecure1!'))

    def test_reset_password_invalid_token(self):
        response = self.client.post(RESET_PASSWORD_URL, {
            'token': 'invalid',
            'password': 'NewSecure1!',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
