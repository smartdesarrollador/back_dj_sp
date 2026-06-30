"""
Tests for bulk bookmark import (POST /api/v1/app/bookmarks/import/).

Covers: feature gating, row creation, partial plan-limit, invalid rows reported
without aborting, row cap, and audit logging.
"""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.audit.models import AuditLog
from apps.bookmarks.models import Bookmark
from apps.tenants.models import Tenant

User = get_user_model()

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

IMPORT_URL = '/api/v1/app/bookmarks/import/'


def _create_tenant(slug, plan='professional'):
    return Tenant.objects.create(name=slug.capitalize(), slug=slug, subdomain=slug, plan=plan)


def _create_superuser(tenant, email):
    user = User.objects.create_user(email=email, name='Test User', password='x', tenant=tenant)
    user.is_superuser = True
    user.save(update_fields=['is_superuser'])
    return user


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestBookmarkImport(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = _create_tenant('bmimport-corp', plan='professional')
        self.user = _create_superuser(self.tenant, 'u@bmimport.com')
        self.client.force_authenticate(user=self.user)
        self.slug = {'HTTP_X_TENANT_SLUG': 'bmimport-corp'}

    def test_import_creates_bookmarks(self):
        items = [
            {'url': 'https://a.com', 'title': 'A', 'tags': ['x']},
            {'url': 'https://b.com', 'title': 'B'},
        ]
        r = self.client.post(IMPORT_URL, {'items': items}, format='json', **self.slug)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        body = r.json()
        self.assertEqual(body['created'], 2)
        self.assertEqual(body['errors'], [])
        self.assertEqual(Bookmark.objects.filter(tenant=self.tenant, user=self.user).count(), 2)

    def test_feature_gate_blocks_free_plan(self):
        self.tenant.plan = 'free'
        self.tenant.save(update_fields=['plan'])
        r = self.client.post(
            IMPORT_URL, {'items': [{'url': 'https://a.com', 'title': 'A'}]}, format='json', **self.slug
        )
        self.assertNotEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn(r.status_code, (status.HTTP_402_PAYMENT_REQUIRED, status.HTTP_403_FORBIDDEN))

    def test_partial_plan_limit(self):
        # Starter: max_bookmarks = 100. Import 103 → 100 created, 3 skipped.
        self.tenant.plan = 'starter'
        self.tenant.save(update_fields=['plan'])
        items = [{'url': f'https://x{i}.com', 'title': f'B{i}'} for i in range(103)]
        r = self.client.post(IMPORT_URL, {'items': items}, format='json', **self.slug)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        body = r.json()
        self.assertEqual(body['created'], 100)
        self.assertEqual(body['skipped'], 3)

    def test_invalid_rows_reported_without_aborting(self):
        items = [{'url': 'https://ok.com', 'title': 'OK'}, {'title': 'no url'}]  # 2nd lacks url
        r = self.client.post(IMPORT_URL, {'items': items}, format='json', **self.slug)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        body = r.json()
        self.assertEqual(body['created'], 1)
        self.assertEqual(len(body['errors']), 1)
        self.assertEqual(body['errors'][0]['index'], 1)

    def test_row_cap(self):
        items = [{'url': 'https://x.com', 'title': 'x'} for _ in range(1001)]
        r = self.client.post(IMPORT_URL, {'items': items}, format='json', **self.slug)
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_import_is_audited(self):
        self.client.post(
            IMPORT_URL, {'items': [{'url': 'https://a.com', 'title': 'A'}]}, format='json', **self.slug
        )
        log = AuditLog.objects.filter(action='bookmarks.import', resource_type='Bookmark').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.extra['created'], 1)
