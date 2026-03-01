"""
Tests for PASO 12 — Bookmarks module.
Covers: list, create with tags, plan limit, collection feature gate, cross-tenant isolation.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.bookmarks.models import Bookmark
from apps.tenants.models import Tenant

User = get_user_model()

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

BASE_URL = '/api/v1/app/bookmarks/'


def _create_tenant(slug, plan='professional'):
    return Tenant.objects.create(name=slug.capitalize(), slug=slug, subdomain=slug, plan=plan)


def _create_superuser(tenant, email):
    user = User.objects.create_user(
        email=email, name='Test User', password='x', tenant=tenant
    )
    user.is_superuser = True
    user.save(update_fields=['is_superuser'])
    return user


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestBookmarkViews(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = _create_tenant('bm-corp')
        self.user = _create_superuser(self.tenant, 'u@bm.com')
        self.client.force_authenticate(user=self.user)
        self.slug = {'HTTP_X_TENANT_SLUG': 'bm-corp'}

    # ── List ──────────────────────────────────────────────────────────────────

    def test_list_bookmarks_empty(self):
        response = self.client.get(BASE_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['bookmarks'], [])

    # ── Create with tags ──────────────────────────────────────────────────────

    def test_create_bookmark_with_tags(self):
        data = {
            'url': 'https://example.com',
            'title': 'Example',
            'tags': ['python', 'tools'],
        }
        response = self.client.post(BASE_URL, data, format='json', **self.slug)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        body = response.json()
        self.assertEqual(body['title'], 'Example')
        self.assertEqual(sorted(body['tags']), ['python', 'tools'])
        bm = Bookmark.objects.get(tenant=self.tenant, title='Example')
        self.assertIn('python', bm.tags)

    # ── Plan limit ────────────────────────────────────────────────────────────

    def test_create_bookmark_exceeds_plan_limit(self):
        with patch('apps.bookmarks.views.check_plan_limit') as mock_limit:
            from core.exceptions import PlanLimitExceeded
            mock_limit.side_effect = PlanLimitExceeded()
            data = {'url': 'https://example.com', 'title': 'X'}
            response = self.client.post(BASE_URL, data, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)

    # ── Collection feature gate ────────────────────────────────────────────────

    def test_bookmark_collection_requires_feature(self):
        """Free plan cannot access bookmark collections endpoint."""
        free_tenant = _create_tenant('free-bm', plan='free')
        free_user = _create_superuser(free_tenant, 'free@bm.com')
        self.client.force_authenticate(user=free_user)
        response = self.client.get(
            BASE_URL + 'collections/', HTTP_X_TENANT_SLUG='free-bm'
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # ── Cross-tenant isolation ────────────────────────────────────────────────

    def test_cross_tenant_bookmark_blocked(self):
        other_tenant = _create_tenant('other-bm')
        other_user = _create_superuser(other_tenant, 'other@bm.com')
        bm = Bookmark.objects.create(
            tenant=other_tenant,
            user=other_user,
            url='https://other.com',
            title='Other Bookmark',
        )
        url = f'{BASE_URL}{bm.pk}/'
        response = self.client.get(url, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
