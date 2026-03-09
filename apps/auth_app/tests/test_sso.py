"""
Tests for SSO endpoints — SSOTokenView and SSOValidateView.

Covers: token generation, validation, expiry, single-use enforcement, audit logs,
        JWT claims, cleanup task, tenant/service gating.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import UntypedToken

from apps.audit.models import AuditLog
from apps.auth_app.models import SSOToken
from apps.services.models import Service, TenantService
from apps.tenants.models import Tenant

User = get_user_model()

SSO_TOKEN_URL = '/api/v1/auth/sso/token/'
SSO_VALIDATE_URL = '/api/v1/auth/sso/validate/'


def _create_tenant(slug='hub-corp'):
    return Tenant.objects.create(name='Hub Corp', slug=slug, subdomain=slug)


def _create_user(tenant, email='hub@test.com', password='ValidPass1!', verified=True):
    user = User.objects.create_user(
        email=email, name='Hub User', password=password, tenant=tenant
    )
    if verified:
        user.email_verified = True
        user.save(update_fields=['email_verified'])
    return user


def _create_service(slug='workspace', min_plan='free', is_active=True):
    return Service.objects.create(
        slug=slug,
        name='Workspace',
        icon='LayoutDashboard',
        url_template='https://{subdomain}.workspace.app',
        min_plan=min_plan,
        is_active=is_active,
    )


def _create_tenant_service(tenant, service, svc_status='active'):
    return TenantService.objects.create(
        tenant=tenant,
        service=service,
        status=svc_status,
    )


def _create_sso_token(user, tenant, token_str='a' * 64, seconds_until_expiry=60, used_at=None):
    return SSOToken.objects.create(
        user=user,
        tenant=tenant,
        service='workspace',
        token=token_str,
        expires_at=timezone.now() + timedelta(seconds=seconds_until_expiry),
        used_at=used_at,
    )


class TestSSOTokenView(TestCase):
    """Tests for POST /api/v1/auth/sso/token/ — token generation."""

    def setUp(self):
        self.client = APIClient()
        self.tenant = _create_tenant()
        self.user = _create_user(self.tenant)
        self.service = _create_service()
        self.client.force_authenticate(user=self.user)

    def test_generate_token_success(self):
        _create_tenant_service(self.tenant, self.service)
        response = self.client.post(SSO_TOKEN_URL, {'service': 'workspace'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(len(data['sso_token']), 64)
        self.assertIn('workspace.app', data['redirect_url'])
        self.assertEqual(data['expires_in'], 60)

    def test_generate_token_creates_db_row(self):
        _create_tenant_service(self.tenant, self.service)
        response = self.client.post(SSO_TOKEN_URL, {'service': 'workspace'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(SSOToken.objects.filter(service='workspace').exists())

    def test_generate_token_creates_audit_log(self):
        _create_tenant_service(self.tenant, self.service)
        response = self.client.post(SSO_TOKEN_URL, {'service': 'workspace'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(AuditLog.objects.filter(action='sso.token_created').exists())

    def test_service_not_found_returns_404(self):
        response = self.client.post(SSO_TOKEN_URL, {'service': 'nonexistent'})
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_inactive_service_returns_404(self):
        inactive = _create_service(slug='inactive-svc', is_active=False)
        _create_tenant_service(self.tenant, inactive)
        response = self.client.post(SSO_TOKEN_URL, {'service': 'inactive-svc'})
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_tenant_service_not_acquired_returns_403(self):
        # service exists but no TenantService row
        response = self.client.post(SSO_TOKEN_URL, {'service': 'workspace'})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_tenant_service_suspended_returns_403(self):
        _create_tenant_service(self.tenant, self.service, svc_status='suspended')
        response = self.client.post(SSO_TOKEN_URL, {'service': 'workspace'})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_suspended_tenant_returns_403(self):
        _create_tenant_service(self.tenant, self.service)
        self.tenant.is_active = False
        self.tenant.save(update_fields=['is_active'])
        response = self.client.post(SSO_TOKEN_URL, {'service': 'workspace'})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_returns_401(self):
        client = APIClient()
        response = client.post(SSO_TOKEN_URL, {'service': 'workspace'})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_missing_service_field_returns_400(self):
        response = self.client.post(SSO_TOKEN_URL, {})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_redirect_url_contains_subdomain(self):
        _create_tenant_service(self.tenant, self.service)
        response = self.client.post(SSO_TOKEN_URL, {'service': 'workspace'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn(self.tenant.subdomain, response.json()['redirect_url'])


class TestSSOValidateView(TestCase):
    """Tests for POST /api/v1/auth/sso/validate/ — token validation."""

    def setUp(self):
        self.client = APIClient()
        self.tenant = _create_tenant(slug='val-corp')
        self.user = _create_user(self.tenant, email='val@test.com')
        self.service = _create_service(slug='workspace2')
        _create_tenant_service(self.tenant, self.service)

    def _make_valid_token(self, token_str=None):
        token_str = token_str or 'v' * 64
        return _create_sso_token(self.user, self.tenant, token_str=token_str)

    def test_happy_path_returns_jwt(self):
        sso_token = self._make_valid_token()
        response = self.client.post(SSO_VALIDATE_URL, {'sso_token': sso_token.token})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('access_token', data)
        self.assertIn('refresh_token', data)
        self.assertIn('user', data)

    def test_happy_path_marks_token_used(self):
        sso_token = self._make_valid_token()
        self.client.post(SSO_VALIDATE_URL, {'sso_token': sso_token.token})
        sso_token.refresh_from_db()
        self.assertIsNotNone(sso_token.used_at)

    def test_happy_path_creates_audit_log(self):
        sso_token = self._make_valid_token()
        self.client.post(SSO_VALIDATE_URL, {'sso_token': sso_token.token})
        self.assertTrue(AuditLog.objects.filter(action='sso.token_validated').exists())

    def test_already_used_token_returns_410(self):
        used_token = _create_sso_token(
            self.user, self.tenant,
            token_str='u' * 64,
            used_at=timezone.now() - timedelta(minutes=5),
        )
        response = self.client.post(SSO_VALIDATE_URL, {'sso_token': used_token.token})
        self.assertEqual(response.status_code, status.HTTP_410_GONE)

    def test_already_used_creates_invalid_log(self):
        used_token = _create_sso_token(
            self.user, self.tenant,
            token_str='u2' * 32,
            used_at=timezone.now() - timedelta(minutes=5),
        )
        self.client.post(SSO_VALIDATE_URL, {'sso_token': used_token.token})
        self.assertTrue(
            AuditLog.objects.filter(
                action='sso.token_invalid',
                extra__reason='already_used',
            ).exists()
        )

    def test_expired_token_returns_410(self):
        expired_token = _create_sso_token(
            self.user, self.tenant,
            token_str='e' * 64,
            seconds_until_expiry=-10,
        )
        response = self.client.post(SSO_VALIDATE_URL, {'sso_token': expired_token.token})
        self.assertEqual(response.status_code, status.HTTP_410_GONE)

    def test_expired_token_is_deleted(self):
        expired_token = _create_sso_token(
            self.user, self.tenant,
            token_str='e2' * 32,
            seconds_until_expiry=-10,
        )
        token_value = expired_token.token
        self.client.post(SSO_VALIDATE_URL, {'sso_token': token_value})
        self.assertFalse(SSOToken.objects.filter(token=token_value).exists())

    def test_nonexistent_token_returns_404(self):
        response = self.client.post(SSO_VALIDATE_URL, {'sso_token': 'x' * 64})
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_jwt_contains_service_claim(self):
        sso_token = self._make_valid_token(token_str='j' * 64)
        response = self.client.post(SSO_VALIDATE_URL, {'sso_token': sso_token.token})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        token = UntypedToken(response.json()['access_token'])
        self.assertEqual(token['service'], 'workspace')

    def test_concurrency_second_call_returns_410(self):
        sso_token = self._make_valid_token(token_str='c' * 64)
        r1 = self.client.post(SSO_VALIDATE_URL, {'sso_token': sso_token.token})
        r2 = self.client.post(SSO_VALIDATE_URL, {'sso_token': sso_token.token})
        self.assertEqual(r1.status_code, status.HTTP_200_OK)
        self.assertEqual(r2.status_code, status.HTTP_410_GONE)

    def test_no_auth_required_for_validate(self):
        """SSOValidateView has no authentication — works without Bearer token."""
        sso_token = self._make_valid_token(token_str='n' * 64)
        # Deliberately not authenticating the client
        anon_client = APIClient()
        response = anon_client.post(SSO_VALIDATE_URL, {'sso_token': sso_token.token})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_missing_sso_token_field_returns_400(self):
        response = self.client.post(SSO_VALIDATE_URL, {})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class TestCleanupTask(TestCase):
    """Tests for the Celery cleanup task."""

    def setUp(self):
        self.tenant = _create_tenant(slug='cleanup-corp')
        self.user = _create_user(self.tenant, email='cleanup@test.com')

    def test_cleanup_deletes_expired_unused(self):
        SSOToken.objects.create(
            user=self.user, tenant=self.tenant, service='workspace',
            token='d' * 64,
            expires_at=timezone.now() - timedelta(minutes=2),
        )
        from apps.auth_app.tasks import cleanup_expired_sso_tokens
        result = cleanup_expired_sso_tokens()
        self.assertGreaterEqual(result['expired_unused_deleted'], 1)

    def test_cleanup_deletes_old_used(self):
        SSOToken.objects.create(
            user=self.user, tenant=self.tenant, service='workspace',
            token='f' * 64,
            expires_at=timezone.now() + timedelta(seconds=60),
            used_at=timezone.now() - timedelta(hours=2),
        )
        from apps.auth_app.tasks import cleanup_expired_sso_tokens
        result = cleanup_expired_sso_tokens()
        self.assertGreaterEqual(result['used_old_deleted'], 1)

    def test_cleanup_preserves_recent_used(self):
        token = SSOToken.objects.create(
            user=self.user, tenant=self.tenant, service='workspace',
            token='g' * 64,
            expires_at=timezone.now() + timedelta(seconds=60),
            used_at=timezone.now() - timedelta(minutes=30),
        )
        from apps.auth_app.tasks import cleanup_expired_sso_tokens
        cleanup_expired_sso_tokens()
        self.assertTrue(SSOToken.objects.filter(pk=token.pk).exists())

    def test_cleanup_preserves_valid_unused(self):
        token = SSOToken.objects.create(
            user=self.user, tenant=self.tenant, service='workspace',
            token='h' * 64,
            expires_at=timezone.now() + timedelta(seconds=30),
        )
        from apps.auth_app.tasks import cleanup_expired_sso_tokens
        cleanup_expired_sso_tokens()
        self.assertTrue(SSOToken.objects.filter(pk=token.pk).exists())
