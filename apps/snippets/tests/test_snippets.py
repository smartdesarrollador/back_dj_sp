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

from apps.sharing.models import Share
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

    # ── is_favorite / usage_count ────────────────────────────────────────────

    def test_create_snippet_with_is_favorite(self):
        data = {
            'title': 'Favorite one', 'code': 'x = 1', 'language': 'python',
            'is_favorite': True,
        }
        response = self.client.post(BASE_URL, data, format='json', **self.slug)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        body = response.json()
        self.assertTrue(body['is_favorite'])
        self.assertEqual(body['usage_count'], 0)
        snippet = CodeSnippet.objects.get(tenant=self.tenant, title='Favorite one')
        self.assertTrue(snippet.is_favorite)

    def test_update_snippet_toggles_is_favorite(self):
        snippet = CodeSnippet.objects.create(
            tenant=self.tenant, user=self.user, title='X', code='x = 1',
            language='python', is_favorite=False,
        )
        url = f'{BASE_URL}{snippet.pk}/'
        response = self.client.patch(url, {'is_favorite': True}, format='json', **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.json()['is_favorite'])
        snippet.refresh_from_db()
        self.assertTrue(snippet.is_favorite)

    def test_usage_count_is_read_only_on_create(self):
        data = {
            'title': 'Y', 'code': 'x = 1', 'language': 'python', 'usage_count': 999,
        }
        response = self.client.post(BASE_URL, data, format='json', **self.slug)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.json()['usage_count'], 0)

    def test_favorite_snippets_ordered_first(self):
        CodeSnippet.objects.create(
            tenant=self.tenant, user=self.user, title='Old normal', code='x = 1',
            language='python', is_favorite=False,
        )
        CodeSnippet.objects.create(
            tenant=self.tenant, user=self.user, title='Old favorite', code='x = 1',
            language='python', is_favorite=True,
        )
        response = self.client.get(BASE_URL, **self.slug)
        snippets = response.json()['snippets']
        self.assertEqual(snippets[0]['title'], 'Old favorite')

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

    # ── Shared snippets flagging ──────────────────────────────────────────────

    def test_shared_snippet_is_flagged_with_sharer_name(self):
        owner = _create_superuser(self.tenant, 'owner2@sn.com')
        owner.name = 'Snippet Owner'
        owner.save(update_fields=['name'])
        snippet = CodeSnippet.objects.create(
            tenant=self.tenant, user=owner, title='Shared snippet',
            code='x = 1', language='python',
        )
        Share.objects.create(
            tenant=self.tenant,
            resource_type='snippet',
            resource_id=snippet.id,
            shared_by=owner,
            shared_with=self.user,
            permission_level='viewer',
        )
        response = self.client.get(BASE_URL, **self.slug)
        data = next(s for s in response.json()['snippets'] if s['id'] == str(snippet.id))
        self.assertTrue(data['is_shared'])
        self.assertEqual(data['shared_by_name'], 'Snippet Owner')

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

    # ── Filter by tag ─────────────────────────────────────────────────────────

    def test_filter_snippets_by_tag(self):
        CodeSnippet.objects.create(
            tenant=self.tenant, user=self.user, title='A', code='x = 1',
            language='python', tags=['urgente'],
        )
        CodeSnippet.objects.create(
            tenant=self.tenant, user=self.user, title='B', code='y = 2',
            language='python', tags=['personal'],
        )
        response = self.client.get(f'{BASE_URL}?tag=urgente', **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        snippets = response.json()['snippets']
        self.assertEqual(len(snippets), 1)
        self.assertEqual(snippets[0]['title'], 'A')

    # ── Tags: suggestions ────────────────────────────────────────────────────

    def test_snippet_tags_endpoint_returns_distinct_sorted(self):
        CodeSnippet.objects.create(
            tenant=self.tenant, user=self.user, title='A', code='x = 1',
            language='python', tags=['zebra', 'apple'],
        )
        CodeSnippet.objects.create(
            tenant=self.tenant, user=self.user, title='B', code='y = 2',
            language='python', tags=['apple', 'mango'],
        )
        response = self.client.get(f'{BASE_URL}tags/', **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['tags'], ['apple', 'mango', 'zebra'])

    def test_snippet_tags_endpoint_scoped_to_user(self):
        CodeSnippet.objects.create(
            tenant=self.tenant, user=self.user, title='Mine', code='x = 1',
            language='python', tags=['mine'],
        )
        other_user = _create_superuser(self.tenant, 'other-user@sn.com')
        CodeSnippet.objects.create(
            tenant=self.tenant, user=other_user, title='Theirs', code='y = 2',
            language='python', tags=['theirs'],
        )
        response = self.client.get(f'{BASE_URL}tags/', **self.slug)
        self.assertEqual(response.json()['tags'], ['mine'])

    def test_snippet_tags_endpoint_empty_state(self):
        response = self.client.get(f'{BASE_URL}tags/', **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['tags'], [])

    # ── List snippets (pagination) ───────────────────────────────────────────

    def _create_snippets(self, n, **overrides):
        snippets = []
        for i in range(n):
            defaults = {
                'tenant': self.tenant,
                'user': self.user,
                'title': f'Snippet {i}',
                'code': f'x = {i}',
                'language': 'python',
            }
            defaults.update(overrides)
            snippets.append(CodeSnippet.objects.create(**defaults))
        return snippets

    def test_list_snippets_without_page_returns_plain_shape(self):
        self._create_snippets(5)
        response = self.client.get(BASE_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(len(body['snippets']), 5)
        self.assertNotIn('pagination', body)

    def test_list_snippets_first_page_default_per_page(self):
        self._create_snippets(25)
        response = self.client.get(BASE_URL, {'page': 1}, **self.slug)
        body = response.json()
        self.assertEqual(len(body['snippets']), 20)
        self.assertEqual(body['pagination'], {'page': 1, 'per_page': 20, 'total': 25})

    def test_list_snippets_second_page(self):
        self._create_snippets(25)
        response = self.client.get(BASE_URL, {'page': 2}, **self.slug)
        body = response.json()
        self.assertEqual(len(body['snippets']), 5)
        self.assertEqual(body['pagination']['page'], 2)

    def test_list_snippets_custom_per_page(self):
        self._create_snippets(10)
        response = self.client.get(BASE_URL, {'page': 1, 'per_page': 5}, **self.slug)
        body = response.json()
        self.assertEqual(len(body['snippets']), 5)
        self.assertEqual(body['pagination']['per_page'], 5)

    def test_list_snippets_per_page_clamped_to_100(self):
        self._create_snippets(3)
        response = self.client.get(BASE_URL, {'page': 1, 'per_page': 500}, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['pagination']['per_page'], 100)

    def test_list_snippets_page_out_of_range_returns_empty(self):
        self._create_snippets(3)
        response = self.client.get(BASE_URL, {'page': 999}, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body['snippets'], [])
        self.assertEqual(body['pagination']['total'], 3)

    def test_list_snippets_invalid_page_falls_back_to_default(self):
        self._create_snippets(3)
        response = self.client.get(BASE_URL, {'page': 'abc'}, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['pagination']['page'], 1)

    def test_list_snippets_invalid_per_page_falls_back_to_default(self):
        self._create_snippets(3)
        response = self.client.get(BASE_URL, {'page': 1, 'per_page': 'xyz'}, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['pagination']['per_page'], 20)

    def test_list_snippets_negative_page_clamped_to_one(self):
        self._create_snippets(3)
        response = self.client.get(BASE_URL, {'page': -5}, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['pagination']['page'], 1)

    def test_list_snippets_filters_combined_with_pagination(self):
        self._create_snippets(3, language='python')
        self._create_snippets(4, language='javascript')
        response = self.client.get(
            BASE_URL, {'language': 'python', 'page': 1, 'per_page': 2}, **self.slug
        )
        body = response.json()
        self.assertEqual(len(body['snippets']), 2)
        self.assertEqual(body['pagination']['total'], 3)
        self.assertTrue(all(s['language'] == 'python' for s in body['snippets']))

    def test_list_snippets_cross_tenant_pagination_isolated(self):
        other_tenant = _create_tenant('other-snippets-pagination')
        other_user = _create_superuser(other_tenant, 'other@snippets-pagination.com')
        CodeSnippet.objects.create(
            tenant=other_tenant, user=other_user, title='Other tenant', code='x = 1',
            language='python',
        )
        self._create_snippets(2)
        response = self.client.get(BASE_URL, {'page': 1}, **self.slug)
        self.assertEqual(response.json()['pagination']['total'], 2)
