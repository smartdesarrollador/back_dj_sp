"""
Tests for PASO 13 — Snippets module.
Covers: list, create with tags (ArrayField), plan limit, filter by language, cross-tenant.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.snippets.models import CodeSnippet
from apps.tenants.models import Tenant

User = get_user_model()

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

BASE_URL = '/api/v1/app/snippets/'


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
class TestSnippetViews(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = _create_tenant('sn-corp')
        self.user = _create_superuser(self.tenant, 'u@sn.com')
        self.client.force_authenticate(user=self.user)
        self.slug = {'HTTP_X_TENANT_SLUG': 'sn-corp'}

    # ── List ──────────────────────────────────────────────────────────────────

    def test_list_snippets_empty(self):
        response = self.client.get(BASE_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['snippets'], [])

    # ── Create with tags ──────────────────────────────────────────────────────

    def test_create_snippet_with_tags(self):
        data = {
            'title': 'Hello World',
            'code': 'print("Hello")',
            'language': 'python',
            'tags': ['beginner', 'tutorial'],
        }
        response = self.client.post(BASE_URL, data, format='json', **self.slug)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        body = response.json()
        self.assertEqual(body['title'], 'Hello World')
        self.assertEqual(body['language'], 'python')
        self.assertEqual(sorted(body['tags']), ['beginner', 'tutorial'])
        snippet = CodeSnippet.objects.get(tenant=self.tenant, title='Hello World')
        self.assertIn('beginner', snippet.tags)
        self.assertIn('tutorial', snippet.tags)

    # ── Plan limit ────────────────────────────────────────────────────────────

    def test_create_snippet_exceeds_plan_limit(self):
        with patch('apps.snippets.views.check_plan_limit') as mock_limit:
            from core.exceptions import PlanLimitExceeded
            mock_limit.side_effect = PlanLimitExceeded()
            data = {'title': 'X', 'code': 'x = 1', 'language': 'python'}
            response = self.client.post(BASE_URL, data, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)

    # ── Filter by language ────────────────────────────────────────────────────

    def test_snippet_filter_by_language(self):
        CodeSnippet.objects.create(
            tenant=self.tenant, user=self.user,
            title='Python snippet', code='x = 1', language='python',
        )
        CodeSnippet.objects.create(
            tenant=self.tenant, user=self.user,
            title='JS snippet', code='const x = 1;', language='javascript',
        )
        response = self.client.get(BASE_URL + '?language=python', **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        snippets = response.json()['snippets']
        self.assertEqual(len(snippets), 1)
        self.assertEqual(snippets[0]['language'], 'python')

    # ── Cross-tenant isolation ────────────────────────────────────────────────

    def test_cross_tenant_snippet_blocked(self):
        other_tenant = _create_tenant('other-sn')
        other_user = _create_superuser(other_tenant, 'other@sn.com')
        snippet = CodeSnippet.objects.create(
            tenant=other_tenant,
            user=other_user,
            title='Other snippet',
            code='x = 1',
            language='python',
        )
        url = f'{BASE_URL}{snippet.pk}/'
        response = self.client.get(url, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
