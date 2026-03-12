"""
Tests for PASO 23 — Referral system: ReferralCode, Referral, and endpoints.
Covers: dashboard endpoint, code creation, stats, tenant isolation, register flow, celery task.
"""
import uuid
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.referrals.models import Referral, ReferralCode
from apps.referrals.tasks import activate_pending_referrals
from apps.subscriptions.models import Subscription
from apps.tenants.models import Tenant

User = get_user_model()

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

REFERRAL_URL = '/api/v1/app/referrals/'
REGISTER_URL = '/api/v1/auth/register'


def _create_tenant(slug='test-corp', plan='free', name='Test Corp', subdomain=None):
    return Tenant.objects.create(
        name=name,
        slug=slug,
        subdomain=subdomain or slug,
        plan=plan,
    )


def _create_user(tenant, email='owner@test.com', superuser=False):
    if superuser:
        user = User.objects.create_superuser(
            email=email, name='Owner', password='Password123!', tenant=tenant,
        )
    else:
        user = User.objects.create_user(
            email=email, name='Owner', password='Password123!', tenant=tenant,
        )
    user.email_verified = True
    user.save(update_fields=['email_verified'])
    return user


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestReferralDashboard(APITestCase):

    def setUp(self):
        cache.clear()
        self.tenant = _create_tenant()
        # Use superuser to bypass HasPermission('referrals.read') until fixtures are loaded in PASO 26
        self.user = _create_user(self.tenant, superuser=True)
        self.client.force_authenticate(user=self.user)
        self.slug = {'HTTP_X_TENANT_SLUG': self.tenant.slug}

    def test_get_referral_dashboard_returns_code_and_stats(self):
        response = self.client.get(REFERRAL_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('code', data)
        self.assertIn('referral_url', data)
        self.assertIn('stats', data)
        self.assertIn('referrals', data)
        self.assertIn('referred', data['stats'])
        self.assertIn('credits_earned', data['stats'])
        self.assertIn('available_credits', data['stats'])

    def test_get_creates_code_if_missing(self):
        self.assertFalse(ReferralCode.objects.filter(tenant=self.tenant).exists())
        response = self.client.get(REFERRAL_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(ReferralCode.objects.filter(tenant=self.tenant).exists())

    def test_get_code_is_idempotent(self):
        response1 = self.client.get(REFERRAL_URL, **self.slug)
        response2 = self.client.get(REFERRAL_URL, **self.slug)
        self.assertEqual(response1.json()['code'], response2.json()['code'])
        self.assertEqual(ReferralCode.objects.filter(tenant=self.tenant).count(), 1)

    def test_stats_counts_all_referrals(self):
        other = _create_tenant(slug='other-corp', name='Other Corp', subdomain='other')
        another = _create_tenant(slug='another-corp', name='Another Corp', subdomain='another')
        Referral.objects.create(referrer=self.tenant, referred=other, status='pending')
        Referral.objects.create(referrer=self.tenant, referred=another, status='active')
        response = self.client.get(REFERRAL_URL, **self.slug)
        self.assertEqual(response.json()['stats']['referred'], 2)

    def test_stats_credits_earned_sums_active_only(self):
        other = _create_tenant(slug='other-corp', name='Other Corp', subdomain='other')
        another = _create_tenant(slug='another-corp', name='Another Corp', subdomain='another')
        Referral.objects.create(
            referrer=self.tenant, referred=other, status='active', credit_amount=Decimal('29.00')
        )
        Referral.objects.create(
            referrer=self.tenant, referred=another, status='pending', credit_amount=Decimal('29.00')
        )
        response = self.client.get(REFERRAL_URL, **self.slug)
        # Only active referral counts towards credits_earned
        self.assertEqual(Decimal(response.json()['stats']['credits_earned']), Decimal('29.00'))

    def test_stats_available_credits_from_subscription(self):
        # Signal auto-creates Subscription on Tenant creation — update it
        Subscription.objects.filter(tenant=self.tenant).update(
            plan='starter', status='active', credit_balance=Decimal('58.00')
        )
        response = self.client.get(REFERRAL_URL, **self.slug)
        self.assertEqual(Decimal(response.json()['stats']['available_credits']), Decimal('58.00'))

    def test_referral_url_contains_code(self):
        response = self.client.get(REFERRAL_URL, **self.slug)
        data = response.json()
        self.assertIn(data['code'], data['referral_url'])
        self.assertIn('ref=', data['referral_url'])

    def test_requires_authentication(self):
        self.client.force_authenticate(user=None)
        response = self.client.get(REFERRAL_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_requires_tenant_header(self):
        self.client.raise_request_exception = False
        response = self.client.get(REFERRAL_URL)
        # TenantMiddleware sets request.tenant=None when header missing; view returns 4xx/5xx
        self.assertGreaterEqual(response.status_code, 400)
        self.client.raise_request_exception = True

    def test_tenant_isolation(self):
        other_tenant = _create_tenant(slug='other-corp', name='Other Corp', subdomain='other')
        third_tenant = _create_tenant(slug='third-corp', name='Third Corp', subdomain='third')
        # Referral for other_tenant's referrals — should NOT appear in self.tenant's view
        Referral.objects.create(referrer=other_tenant, referred=third_tenant, status='pending')
        response = self.client.get(REFERRAL_URL, **self.slug)
        self.assertEqual(response.json()['stats']['referred'], 0)
        self.assertEqual(response.json()['referrals'], [])


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestRegisterWithRefCode(APITestCase):

    def setUp(self):
        cache.clear()
        # Create a referrer tenant with an existing code
        self.referrer = _create_tenant(slug='referrer-corp', name='Referrer Corp', subdomain='referrer')
        self.ref_code = ReferralCode.objects.create(
            tenant=self.referrer,
            code='REF-REFERRER-TEST',
        )

    def test_register_with_valid_ref_code_creates_referral(self):
        payload = {
            'name': 'New User',
            'email': 'newuser@example.com',
            'password': 'StrongPass123!',
            'organization_name': 'New Org',
            'ref_code': 'REF-REFERRER-TEST',
        }
        response = self.client.post(REGISTER_URL, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(Referral.objects.filter(referrer=self.referrer).exists())
        referral = Referral.objects.get(referrer=self.referrer)
        self.assertEqual(referral.status, 'pending')

    def test_register_with_invalid_ref_code_succeeds_silently(self):
        payload = {
            'name': 'New User',
            'email': 'newuser2@example.com',
            'password': 'StrongPass123!',
            'organization_name': 'New Org 2',
            'ref_code': 'REF-INVALID-XXXX',
        }
        response = self.client.post(REGISTER_URL, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        # No referral created for invalid code
        self.assertFalse(Referral.objects.exists())

    def test_register_creates_referral_code_for_new_tenant(self):
        payload = {
            'name': 'New User',
            'email': 'newuser3@example.com',
            'password': 'StrongPass123!',
            'organization_name': 'Brand New Org',
        }
        response = self.client.post(REGISTER_URL, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        # The newly created tenant should have its own ReferralCode
        new_tenant = Tenant.objects.get(slug__icontains='brand-new')
        self.assertTrue(ReferralCode.objects.filter(tenant=new_tenant).exists())


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestActivatePendingReferralsTask(APITestCase):

    def setUp(self):
        cache.clear()
        self.referrer = _create_tenant(slug='referrer-a', name='Referrer A', subdomain='referrer-a')
        self.referred = _create_tenant(slug='referred-b', name='Referred B', subdomain='referred-b')
        # Signal auto-creates Subscription on Tenant creation — update them
        Subscription.objects.filter(tenant=self.referrer).update(
            plan='starter', status='active', credit_balance=Decimal('0.00')
        )
        Subscription.objects.filter(tenant=self.referred).update(
            plan='starter', status='active'
        )

    def test_activate_task_activates_after_7_days(self):
        referral = Referral.objects.create(
            referrer=self.referrer,
            referred=self.referred,
            status='pending',
            credit_amount=Decimal('29.00'),
        )
        # Backdate creation to 8 days ago
        Referral.objects.filter(pk=referral.pk).update(
            created_at=timezone.now() - timedelta(days=8)
        )
        result = activate_pending_referrals()
        self.assertEqual(result['activated'], 1)
        referral.refresh_from_db()
        self.assertEqual(referral.status, 'active')
        sub = Subscription.objects.get(tenant=self.referrer)
        self.assertEqual(sub.credit_balance, Decimal('29.00'))

    def test_activate_task_ignores_recent_referrals(self):
        Referral.objects.create(
            referrer=self.referrer,
            referred=self.referred,
            status='pending',
            credit_amount=Decimal('29.00'),
        )
        # created_at is now (default) — less than 7 days
        result = activate_pending_referrals()
        self.assertEqual(result['activated'], 0)
        sub = Subscription.objects.get(tenant=self.referrer)
        self.assertEqual(sub.credit_balance, Decimal('0.00'))


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestReferralPermissionEnforcement(APITestCase):
    """Verifica que referrals.read se aplica correctamente (post PASO 26)."""

    def setUp(self) -> None:
        cache.clear()
        self.tenant = _create_tenant(slug=f't-{uuid.uuid4().hex[:6]}')
        self.user = _create_user(self.tenant, email=f'u-{uuid.uuid4().hex[:6]}@t.com')
        self.client.force_authenticate(user=self.user)

    def _grant_referrals_read(self) -> None:
        perm, _ = Permission.objects.get_or_create(
            codename='referrals.read',
            defaults={'name': 'Ver Referidos', 'resource': 'referrals', 'action': 'read'},
        )
        role = Role.objects.create(tenant=self.tenant, name='hub-user')
        RolePermission.objects.create(role=role, permission=perm, scope='all')
        UserRole.objects.create(user=self.user, role=role)

    def test_user_without_referrals_read_gets_403(self) -> None:
        resp = self.client.get(REFERRAL_URL, HTTP_X_TENANT_SLUG=self.tenant.slug)
        self.assertEqual(resp.status_code, 403)

    def test_user_with_referrals_read_gets_200(self) -> None:
        self._grant_referrals_read()
        resp = self.client.get(REFERRAL_URL, HTTP_X_TENANT_SLUG=self.tenant.slug)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('code', resp.data)
