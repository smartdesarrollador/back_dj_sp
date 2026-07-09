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

    # ── Filter by tag ─────────────────────────────────────────────────────────

    def test_filter_bookmarks_by_tag(self):
        Bookmark.objects.create(
            tenant=self.tenant, user=self.user, url='https://a.com', title='A',
            tags=['urgente'],
        )
        Bookmark.objects.create(
            tenant=self.tenant, user=self.user, url='https://b.com', title='B',
            tags=['personal'],
        )
        response = self.client.get(f'{BASE_URL}?tag=urgente', **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        bookmarks = response.json()['bookmarks']
        self.assertEqual(len(bookmarks), 1)
        self.assertEqual(bookmarks[0]['title'], 'A')

    # ── Tags: suggestions ────────────────────────────────────────────────────

    def test_bookmark_tags_endpoint_returns_distinct_sorted(self):
        Bookmark.objects.create(
            tenant=self.tenant, user=self.user, url='https://a.com', title='A',
            tags=['zebra', 'apple'],
        )
        Bookmark.objects.create(
            tenant=self.tenant, user=self.user, url='https://b.com', title='B',
            tags=['apple', 'mango'],
        )
        response = self.client.get(f'{BASE_URL}tags/', **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['tags'], ['apple', 'mango', 'zebra'])

    def test_bookmark_tags_endpoint_scoped_to_user(self):
        Bookmark.objects.create(
            tenant=self.tenant, user=self.user, url='https://a.com', title='Mine',
            tags=['mine'],
        )
        other_user = _create_superuser(self.tenant, 'other-user@bm.com')
        Bookmark.objects.create(
            tenant=self.tenant, user=other_user, url='https://b.com', title='Theirs',
            tags=['theirs'],
        )
        response = self.client.get(f'{BASE_URL}tags/', **self.slug)
        self.assertEqual(response.json()['tags'], ['mine'])

    def test_bookmark_tags_endpoint_empty_state(self):
        response = self.client.get(f'{BASE_URL}tags/', **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['tags'], [])

    # ── is_favorite ───────────────────────────────────────────────────────────

    def test_create_bookmark_with_is_favorite(self):
        data = {
            'url': 'https://fav.com', 'title': 'Favorite one', 'is_favorite': True,
        }
        response = self.client.post(BASE_URL, data, format='json', **self.slug)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.json()['is_favorite'])
        bm = Bookmark.objects.get(tenant=self.tenant, title='Favorite one')
        self.assertTrue(bm.is_favorite)

    def test_update_bookmark_toggles_is_favorite(self):
        bm = Bookmark.objects.create(
            tenant=self.tenant, user=self.user, url='https://x.com', title='X',
            is_favorite=False,
        )
        url = f'{BASE_URL}{bm.pk}/'
        response = self.client.patch(url, {'is_favorite': True}, format='json', **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.json()['is_favorite'])
        bm.refresh_from_db()
        self.assertTrue(bm.is_favorite)

    def test_favorite_bookmarks_ordered_first(self):
        Bookmark.objects.create(
            tenant=self.tenant, user=self.user, url='https://old.com', title='Old normal',
            is_favorite=False,
        )
        Bookmark.objects.create(
            tenant=self.tenant, user=self.user, url='https://fav.com', title='Old favorite',
            is_favorite=True,
        )
        response = self.client.get(BASE_URL, **self.slug)
        bookmarks = response.json()['bookmarks']
        self.assertEqual(bookmarks[0]['title'], 'Old favorite')

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
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)

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
