"""
Tests for OrganizationView — own tenant branding (name, color, logo, favicon).
Covers: storage-limit gate on logo/favicon upload.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.tenants.models import Tenant
from core.exceptions import PlanLimitExceeded

User = get_user_model()

ORG_URL = '/api/v1/admin/organization/'

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


def _create_tenant(slug, plan='professional'):
    return Tenant.objects.create(
        name=slug.capitalize(), slug=slug, subdomain=slug, plan=plan
    )


def _create_user(tenant, email):
    return User.objects.create_user(
        email=email, name='Test User', password='pass123', tenant=tenant
    )


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestOrganizationView(APITestCase):
    def setUp(self):
        cache.clear()  # Prevent stale tenant lookups between test savepoints
        self.tenant = _create_tenant('org-corp')
        self.user = _create_user(self.tenant, 'owner@org-corp.com')
        self.client.force_authenticate(user=self.user)

    def test_update_name_success(self):
        response = self.client.patch(
            ORG_URL, {'name': 'New Name'}, format='multipart', HTTP_X_TENANT_SLUG='org-corp'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.name, 'New Name')

    def test_update_logo_success(self):
        logo = SimpleUploadedFile('logo.png', b'\x89PNG fake', content_type='image/png')
        response = self.client.patch(
            ORG_URL, {'logo': logo}, format='multipart', HTTP_X_TENANT_SLUG='org-corp'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.tenant.refresh_from_db()
        self.assertTrue(bool(self.tenant.logo))

    @patch('apps.tenants.admin_views.check_storage_limit')
    def test_logo_over_storage_limit_rejected(self, mock_limit):
        mock_limit.side_effect = PlanLimitExceeded()
        logo = SimpleUploadedFile('logo.png', b'\x89PNG fake', content_type='image/png')
        response = self.client.patch(
            ORG_URL, {'logo': logo}, format='multipart', HTTP_X_TENANT_SLUG='org-corp'
        )
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)
        self.tenant.refresh_from_db()
        self.assertFalse(bool(self.tenant.logo))
