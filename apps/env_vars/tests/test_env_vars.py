"""
Tests for PASO 13 — EnvVars module.
Covers: list, create (value masked), plan limit, reveal plaintext, cross-tenant isolation.
"""
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.env_vars.models import EnvVariable
from apps.tenants.models import Tenant

User = get_user_model()

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
_ENC_KEY = Fernet.generate_key().decode()

BASE_URL = '/api/v1/app/env-vars/'


def _create_tenant(slug, plan='professional'):
    return Tenant.objects.create(name=slug.capitalize(), slug=slug, subdomain=slug, plan=plan)


def _create_superuser(tenant, email):
    user = User.objects.create_user(
        email=email, name='Test User', password='x', tenant=tenant
    )
    user.is_superuser = True
    user.save(update_fields=['is_superuser'])
    return user


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE, ENCRYPTION_KEY=_ENC_KEY)
class TestEnvVarViews(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = _create_tenant('ev-corp')
        self.user = _create_superuser(self.tenant, 'u@ev.com')
        self.client.force_authenticate(user=self.user)
        self.slug = {'HTTP_X_TENANT_SLUG': 'ev-corp'}

    # ── List ──────────────────────────────────────────────────────────────────

    def test_list_env_vars_empty(self):
        response = self.client.get(BASE_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['env_vars'], [])

    # ── Create (value masked) ─────────────────────────────────────────────────

    def test_create_env_var_success(self):
        data = {'key': 'DATABASE_URL', 'value': 'postgres://localhost/db', 'environment': 'production'}
        response = self.client.post(BASE_URL, data, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        body = response.json()
        self.assertEqual(body['key'], 'DATABASE_URL')
        self.assertEqual(body['environment'], 'production')
        # value must NOT appear in the response
        self.assertNotIn('value', body)
        # Verify stored encrypted in DB
        ev = EnvVariable.objects.get(tenant=self.tenant, key='DATABASE_URL')
        self.assertTrue(ev.is_encrypted)
        self.assertNotEqual(ev.value, 'postgres://localhost/db')

    # ── Plan limit ────────────────────────────────────────────────────────────

    def test_create_env_var_exceeds_plan_limit(self):
        with patch('apps.env_vars.views.check_plan_limit') as mock_limit:
            from core.exceptions import PlanLimitExceeded
            mock_limit.side_effect = PlanLimitExceeded()
            response = self.client.post(BASE_URL, {'key': 'X', 'value': 'y'}, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)

    # ── Reveal plaintext ──────────────────────────────────────────────────────

    def test_env_var_reveal_returns_plaintext(self):
        ev = EnvVariable.objects.create(
            tenant=self.tenant,
            user=self.user,
            key='SECRET_KEY',
            value='my-secret-value',
            environment='all',
        )
        url = f'{BASE_URL}{ev.pk}/reveal/'
        response = self.client.post(url, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body['key'], 'SECRET_KEY')
        self.assertEqual(body['value'], 'my-secret-value')

    # ── Cross-tenant isolation ────────────────────────────────────────────────

    def test_cross_tenant_env_var_blocked(self):
        other_tenant = _create_tenant('other-ev')
        other_user = _create_superuser(other_tenant, 'other@ev.com')
        ev = EnvVariable.objects.create(
            tenant=other_tenant,
            user=other_user,
            key='OTHER_KEY',
            value='other-value',
        )
        url = f'{BASE_URL}{ev.pk}/'
        response = self.client.get(url, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
