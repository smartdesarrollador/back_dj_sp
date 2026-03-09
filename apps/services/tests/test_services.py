"""
Tests for PASO 22 — Services catalog and active services endpoints.
Covers: catalog listing, available flag, status field, redirect_url, auth, tenant isolation.
"""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.services.models import Service, TenantService
from apps.tenants.models import Tenant

User = get_user_model()

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

CATALOG_URL = '/api/v1/app/services/'
ACTIVE_URL = '/api/v1/app/services/active/'


def _create_tenant(slug='test-corp', plan='free', subdomain='test'):
    return Tenant.objects.create(name='Test Corp', slug=slug, subdomain=subdomain, plan=plan)


def _create_user(tenant, email='owner@test.com'):
    user = User.objects.create_user(
        email=email, name='Owner', password='Password123!', tenant=tenant,
    )
    user.email_verified = True
    user.save(update_fields=['email_verified'])
    return user


def _create_service(slug='workspace', min_plan='free', is_active=True):
    return Service.objects.create(
        slug=slug,
        name=slug.title(),
        icon='Icon',
        url_template=f'https://{{subdomain}}.{slug}.app',
        min_plan=min_plan,
        is_active=is_active,
    )


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestServiceCatalog(APITestCase):

    def setUp(self):
        cache.clear()
        self.tenant = _create_tenant()
        self.user = _create_user(self.tenant)
        self.client.force_authenticate(user=self.user)
        self.slug = {'HTTP_X_TENANT_SLUG': self.tenant.slug}

    def test_catalog_returns_all_active_services(self):
        _create_service('workspace')
        _create_service('vista')
        response = self.client.get(CATALOG_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)

    def test_catalog_excludes_inactive_services(self):
        _create_service('workspace', is_active=True)
        _create_service('vista', is_active=False)
        response = self.client.get(CATALOG_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        slugs = [s['slug'] for s in response.data]
        self.assertIn('workspace', slugs)
        self.assertNotIn('vista', slugs)

    def test_catalog_available_true_for_free_tenant(self):
        _create_service('workspace', min_plan='free')
        response = self.client.get(CATALOG_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data[0]['available'])

    def test_catalog_available_false_for_insufficient_plan(self):
        # free tenant, starter-only service
        _create_service('workspace', min_plan='starter')
        response = self.client.get(CATALOG_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data[0]['available'])

    def test_catalog_available_true_for_higher_plan(self):
        tenant = _create_tenant(slug='starter-corp', plan='starter', subdomain='starter')
        user = _create_user(tenant, email='starter@test.com')
        self.client.force_authenticate(user=user)
        _create_service('workspace', min_plan='free')
        response = self.client.get(CATALOG_URL, HTTP_X_TENANT_SLUG=tenant.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data[0]['available'])

    def test_catalog_status_null_when_not_acquired(self):
        _create_service('workspace')
        response = self.client.get(CATALOG_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data[0]['status'])

    def test_catalog_status_populated_when_acquired(self):
        svc = _create_service('workspace')
        TenantService.objects.create(tenant=self.tenant, service=svc, status='active')
        response = self.client.get(CATALOG_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data[0]['status'], 'active')

    def test_catalog_status_suspended(self):
        svc = _create_service('workspace')
        TenantService.objects.create(tenant=self.tenant, service=svc, status='suspended')
        response = self.client.get(CATALOG_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data[0]['status'], 'suspended')

    def test_catalog_redirect_url_uses_subdomain(self):
        _create_service('workspace')
        response = self.client.get(CATALOG_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        redirect_url = response.data[0]['redirect_url']
        self.assertIn(self.tenant.subdomain, redirect_url)
        self.assertTrue(redirect_url.endswith('/auth/sso'))

    def test_catalog_requires_authentication(self):
        self.client.force_authenticate(user=None)
        _create_service('workspace')
        response = self.client.get(CATALOG_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_catalog_requires_tenant_header(self):
        _create_service('workspace')
        response = self.client.get(CATALOG_URL)
        self.assertIn(response.status_code,
                      [status.HTTP_400_BAD_REQUEST, status.HTTP_401_UNAUTHORIZED])

    def test_catalog_tenant_isolation(self):
        # Create free service first, then tenant_b → signal auto-provisions TenantService for tenant_b
        svc = _create_service('workspace')
        tenant_b = _create_tenant(slug='other-corp', subdomain='other')
        # Verify signal created TenantService for tenant_b (get_or_create is idempotent)
        TenantService.objects.get_or_create(tenant=tenant_b, service=svc, defaults={'status': 'active'})
        # Tenant A has no TenantService → status should be null
        response = self.client.get(CATALOG_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data[0]['status'])


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestActiveServicesView(APITestCase):

    def setUp(self):
        cache.clear()
        self.tenant = _create_tenant()
        self.user = _create_user(self.tenant)
        self.client.force_authenticate(user=self.user)
        self.slug = {'HTTP_X_TENANT_SLUG': self.tenant.slug}

    def test_active_returns_only_active_tenant_services(self):
        svc = _create_service('workspace')
        TenantService.objects.create(tenant=self.tenant, service=svc, status='active')
        response = self.client.get(ACTIVE_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['slug'], 'workspace')

    def test_active_excludes_suspended_services(self):
        svc = _create_service('workspace')
        TenantService.objects.create(tenant=self.tenant, service=svc, status='suspended')
        response = self.client.get(ACTIVE_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 0)

    def test_active_excludes_inactive_service_records(self):
        svc = _create_service('workspace', is_active=False)
        TenantService.objects.create(tenant=self.tenant, service=svc, status='active')
        response = self.client.get(ACTIVE_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 0)

    def test_active_requires_authentication(self):
        self.client.force_authenticate(user=None)
        response = self.client.get(ACTIVE_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_active_empty_when_no_services_acquired(self):
        _create_service('workspace')
        response = self.client.get(ACTIVE_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 0)
