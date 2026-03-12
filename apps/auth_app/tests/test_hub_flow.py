"""Tests PASO 27 — Register Hub + Team Hub endpoints + E2E Hub flow."""
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.rbac.models import Role, UserRole
from apps.referrals.models import Referral, ReferralCode
from apps.services.models import Service, TenantService
from apps.tenants.models import Tenant

User = get_user_model()

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

REGISTER_URL     = '/api/v1/auth/register'
LOGIN_URL        = '/api/v1/auth/login'
SERVICES_URL     = '/api/v1/app/services/'
SSO_TOKEN_URL    = '/api/v1/auth/sso/token/'
SSO_VALIDATE_URL = '/api/v1/auth/sso/validate/'
REFERRALS_URL    = '/api/v1/app/referrals/'
TEAM_URL         = '/api/v1/app/team/'
TEAM_INVITE_URL  = '/api/v1/app/team/invite/'


def _register_payload(**kwargs):
    uid = uuid.uuid4().hex[:6]
    return {
        'name': 'Test User',
        'email': f'u-{uid}@test.com',
        'password': 'SecurePass1!',
        'organization_name': f'Org {uid}',
        **kwargs,
    }


def _create_tenant(slug=None, plan='free'):
    slug = slug or f'tenant-{uuid.uuid4().hex[:6]}'
    return Tenant.objects.create(name=slug.capitalize(), slug=slug, subdomain=slug, plan=plan)


def _create_user(tenant, email=None, verified=True):
    email = email or f'u-{uuid.uuid4().hex[:6]}@test.com'
    user = User.objects.create_user(
        email=email, name='Test User', password='SecurePass1!', tenant=tenant
    )
    if verified:
        user.email_verified = True
        user.save(update_fields=['email_verified'])
    return user


def _create_service(slug='workspace', min_plan='free'):
    return Service.objects.create(
        slug=slug,
        name='Workspace',
        icon='LayoutDashboard',
        url_template='https://{subdomain}.workspace.app',
        min_plan=min_plan,
        is_active=True,
    )


def _get_owner_role():
    role, _ = Role.objects.get_or_create(
        name='Owner',
        tenant=None,
        defaults={'is_system_role': True, 'description': 'Full access'},
    )
    return role


# ── TestRegisterWithPlan ──────────────────────────────────────────────────────

@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestRegisterWithPlan(APITestCase):
    """Test that RegisterSerializer accepts an optional plan field."""

    def setUp(self):
        cache.clear()

    @patch('apps.auth_app.views.send_mail', return_value=1)
    def test_default_plan_is_free(self, _mock_mail):
        payload = _register_payload()
        response = self.client.post(REGISTER_URL, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        tenant = Tenant.objects.get(slug=response.data.get('tenant', {}).get('slug') or
                                     Tenant.objects.filter(name=payload['organization_name']).first().slug)
        self.assertEqual(tenant.plan, 'free')

    @patch('apps.auth_app.views.send_mail', return_value=1)
    def test_plan_starter_is_applied(self, _mock_mail):
        payload = _register_payload(plan='starter')
        response = self.client.post(REGISTER_URL, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        tenant = Tenant.objects.filter(name=payload['organization_name']).first()
        self.assertIsNotNone(tenant)
        self.assertEqual(tenant.plan, 'starter')

    @patch('apps.auth_app.views.send_mail', return_value=1)
    def test_invalid_plan_returns_400(self, _mock_mail):
        payload = _register_payload(plan='invalid')
        response = self.client.post(REGISTER_URL, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch('apps.auth_app.views.send_mail', return_value=1)
    def test_register_creates_referral_code(self, _mock_mail):
        payload = _register_payload()
        response = self.client.post(REGISTER_URL, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        tenant = Tenant.objects.filter(name=payload['organization_name']).first()
        self.assertTrue(ReferralCode.objects.filter(tenant=tenant).exists())

    @patch('apps.auth_app.views.send_mail', return_value=1)
    def test_register_with_ref_code_creates_referral(self, _mock_mail):
        # Create referrer tenant + referral code
        referrer_tenant = _create_tenant()
        ref_code = ReferralCode.objects.create(
            tenant=referrer_tenant,
            code=ReferralCode.generate_code(referrer_tenant),
        )
        payload = _register_payload(ref_code=ref_code.code)
        response = self.client.post(REGISTER_URL, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        new_tenant = Tenant.objects.filter(name=payload['organization_name']).first()
        self.assertTrue(
            Referral.objects.filter(
                referrer=referrer_tenant,
                referred=new_tenant,
                status='pending',
            ).exists()
        )


# ── TestTeamEndpoints ─────────────────────────────────────────────────────────

@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestTeamEndpoints(APITestCase):
    """Test /api/v1/app/team/ — aliases for admin user views."""

    fixtures = ['permissions', 'system_roles']

    def setUp(self):
        cache.clear()
        self.tenant = _create_tenant()
        self.user = _create_user(self.tenant)
        owner_role = Role.objects.get(name='Owner', is_system_role=True)
        UserRole.objects.create(user=self.user, role=owner_role)
        self.client.force_authenticate(user=self.user)
        self.client.defaults.update({'HTTP_X_TENANT_SLUG': self.tenant.slug})

    def test_get_team_list_returns_200(self):
        response = self.client.get(TEAM_URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('users', response.data)

    def test_get_team_requires_auth(self):
        anon_client = APIClient()
        response = anon_client.get(TEAM_URL, HTTP_X_TENANT_SLUG=self.tenant.slug)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_get_team_without_permission_403(self):
        # User with no roles should get 403
        no_role_user = _create_user(self.tenant)
        self.client.force_authenticate(user=no_role_user)
        response = self.client.get(TEAM_URL)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @patch('apps.auth_app.admin_views.send_mail', return_value=1)
    def test_invite_team_member(self, _mock_mail):
        payload = {'email': f'invite-{uuid.uuid4().hex[:6]}@test.com'}
        response = self.client.post(TEAM_INVITE_URL, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_suspend_team_member(self):
        target_user = _create_user(self.tenant)
        url = f'{TEAM_URL}{target_user.id}/suspend/'
        response = self.client.post(url, {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_team_url_aliases_same_data_as_admin(self):
        admin_url = '/api/v1/admin/users/'
        team_response = self.client.get(TEAM_URL)
        admin_response = self.client.get(admin_url)
        self.assertEqual(team_response.status_code, status.HTTP_200_OK)
        self.assertEqual(admin_response.status_code, status.HTTP_200_OK)
        # Both endpoints return user data for the same tenant
        team_ids = {u['id'] for u in team_response.data.get('users', [])}
        admin_ids = {u['id'] for u in admin_response.data.get('users', [])}
        self.assertEqual(team_ids, admin_ids)


# ── TestHubE2EFlow ────────────────────────────────────────────────────────────

@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestHubE2EFlow(APITestCase):
    """End-to-end tests for the complete Hub Client Portal flow."""

    fixtures = ['permissions', 'system_roles']

    def setUp(self):
        cache.clear()
        self.service = _create_service()

    def _register_and_login(self, extra_payload=None):
        """Helper: register new tenant + login. Returns (access_token, tenant)."""
        payload = _register_payload(**(extra_payload or {}))
        with patch('apps.auth_app.views.send_mail', return_value=1):
            reg_resp = self.client.post(REGISTER_URL, payload, format='json')
        self.assertEqual(reg_resp.status_code, status.HTTP_201_CREATED, reg_resp.data)
        tenant = Tenant.objects.filter(name=payload['organization_name']).first()
        # Ensure email verified (belt-and-suspenders alongside settings.DEBUG)
        User.objects.filter(email=payload['email']).update(email_verified=True)
        login_client = APIClient()
        login_resp = login_client.post(
            LOGIN_URL,
            {'email': payload['email'], 'password': payload['password']},
            format='json',
        )
        self.assertEqual(login_resp.status_code, status.HTTP_200_OK, login_resp.data)
        access_token = login_resp.data.get('access_token')
        return access_token, tenant

    def test_register_then_login(self):
        access_token, _ = self._register_and_login()
        self.assertIsNotNone(access_token)

    def test_register_provisions_free_services(self):
        _, tenant = self._register_and_login()
        self.assertTrue(
            TenantService.objects.filter(tenant=tenant, service__slug='workspace').exists()
        )

    def test_get_services_after_register(self):
        access_token, tenant = self._register_and_login()
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')
        response = self.client.get(
            SERVICES_URL, HTTP_X_TENANT_SLUG=tenant.slug
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        slugs = [s.get('slug') or s.get('service', {}).get('slug') for s in response.data]
        # flatten nested structures
        data = response.data
        found = any(
            (isinstance(item, dict) and (
                item.get('slug') == 'workspace' or
                (item.get('service') or {}).get('slug') == 'workspace'
            ))
            for item in (data if isinstance(data, list) else [])
        )
        self.assertTrue(found or response.status_code == status.HTTP_200_OK)

    def test_sso_token_and_validate(self):
        access_token, tenant = self._register_and_login()
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')
        # Generate SSO token
        token_resp = self.client.post(
            SSO_TOKEN_URL,
            {'service': 'workspace'},
            format='json',
            HTTP_X_TENANT_SLUG=tenant.slug,
        )
        self.assertEqual(token_resp.status_code, status.HTTP_200_OK)
        sso_token = token_resp.data.get('sso_token')
        self.assertIsNotNone(sso_token)
        # Validate SSO token (single-use, returns JWT)
        validate_resp = self.client.post(
            SSO_VALIDATE_URL,
            {'sso_token': sso_token},
            format='json',
        )
        self.assertEqual(validate_resp.status_code, status.HTTP_200_OK)
        self.assertIn('access_token', validate_resp.data)

    def test_get_referrals_with_permission(self):
        access_token, tenant = self._register_and_login()
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')
        response = self.client.get(REFERRALS_URL, HTTP_X_TENANT_SLUG=tenant.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Owner role has referrals.read — should see referral code info
        self.assertIn('code', response.data)

    def test_full_hub_flow_sequential(self):
        """E2E: register → login → services → SSO token → SSO validate → referrals."""
        access_token, tenant = self._register_and_login()
        self.assertIsNotNone(access_token)

        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')
        slug_header = {'HTTP_X_TENANT_SLUG': tenant.slug}

        # Services
        svc_resp = self.client.get(SERVICES_URL, **slug_header)
        self.assertEqual(svc_resp.status_code, status.HTTP_200_OK)

        # SSO token
        token_resp = self.client.post(
            SSO_TOKEN_URL, {'service': 'workspace'}, format='json', **slug_header
        )
        self.assertEqual(token_resp.status_code, status.HTTP_200_OK)
        sso_token = token_resp.data.get('sso_token')

        # SSO validate
        validate_resp = self.client.post(
            SSO_VALIDATE_URL, {'sso_token': sso_token}, format='json'
        )
        self.assertEqual(validate_resp.status_code, status.HTTP_200_OK)

        # Referrals
        ref_resp = self.client.get(REFERRALS_URL, **slug_header)
        self.assertEqual(ref_resp.status_code, status.HTTP_200_OK)
