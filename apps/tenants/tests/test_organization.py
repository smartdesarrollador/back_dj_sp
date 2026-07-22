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
from core.tests.helpers import png_bytes

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
        logo = SimpleUploadedFile('logo.png', png_bytes(), content_type='image/png')
        response = self.client.patch(
            ORG_URL, {'logo': logo}, format='multipart', HTTP_X_TENANT_SLUG='org-corp'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.tenant.refresh_from_db()
        self.assertTrue(bool(self.tenant.logo))

    def test_executable_renamed_to_png_rejected_as_logo(self):
        # Antes de la validación central esto se guardaba como logo del tenant.
        fake = SimpleUploadedFile(
            'logo.png', b'MZ\x90\x00\x03\x00\x00\x00', content_type='image/png'
        )
        response = self.client.patch(
            ORG_URL, {'logo': fake}, format='multipart', HTTP_X_TENANT_SLUG='org-corp'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.tenant.refresh_from_db()
        self.assertFalse(bool(self.tenant.logo))
        # El motivo concreto debe llegar al cliente, no un genérico "Validation error":
        # depende de que el detalle viaje como lista (ver utils/uploads.py).
        self.assertIn('no es una imagen', response.json()['error']['message'])

    def test_executable_extension_rejected_as_favicon(self):
        fake = SimpleUploadedFile('icon.exe', b'MZ\x90\x00', content_type='image/x-icon')
        response = self.client.patch(
            ORG_URL, {'favicon': fake}, format='multipart', HTTP_X_TENANT_SLUG='org-corp'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.tenant.refresh_from_db()
        self.assertFalse(bool(self.tenant.favicon))
        self.assertIn('no está permitido', response.json()['error']['message'])

    # El módulo de origen, no apps.tenants.admin_views: validate_upload importa
    # check_storage_limit dentro de la función.
    @patch('apps.rbac.permissions.check_storage_limit')
    def test_logo_over_storage_limit_rejected(self, mock_limit):
        mock_limit.side_effect = PlanLimitExceeded()
        logo = SimpleUploadedFile('logo.png', png_bytes(), content_type='image/png')
        response = self.client.patch(
            ORG_URL, {'logo': logo}, format='multipart', HTTP_X_TENANT_SLUG='org-corp'
        )
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)
        self.tenant.refresh_from_db()
        self.assertFalse(bool(self.tenant.logo))
