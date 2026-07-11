"""
Tests for ClientListView — Admin Panel "Clientes" list.
Covers: ClientSubscriptionSerializer reflects Tenant.plan (not the possibly
desynced Subscription.plan) — mismo bug que en el Hub, ver plan de fix
"plan del tenant desincronizado".
"""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.subscriptions.models import Subscription
from apps.tenants.models import Tenant

User = get_user_model()

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

CLIENTS_URL = '/api/v1/admin/clients/'


def _create_tenant(slug, plan='professional'):
    return Tenant.objects.create(name=slug.capitalize(), slug=slug, subdomain=slug, plan=plan)


def _create_superuser(tenant, email):
    return User.objects.create_user(
        email=email, name='Owner', password='pass123', tenant=tenant, is_superuser=True
    )


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestClientListView(APITestCase):
    def setUp(self):
        cache.clear()  # Prevent stale tenant lookups between test savepoints
        self.own_tenant = _create_tenant('own-corp')
        self.owner = _create_superuser(self.own_tenant, 'owner@own-corp.com')
        self.client.force_authenticate(user=self.owner)
        self.headers = {'HTTP_X_TENANT_SLUG': 'own-corp'}

    def test_client_plan_reflects_tenant_plan_when_desynced(self):
        client_tenant = _create_tenant('client-corp', plan='professional')
        sub, _ = Subscription.objects.get_or_create(tenant=client_tenant)
        sub.plan = 'free'
        sub.status = 'active'
        sub.save()

        response = self.client.get(CLIENTS_URL, **self.headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        client = next(c for c in response.json()['clients'] if c['slug'] == 'client-corp')
        self.assertEqual(client['subscription']['plan'], 'professional')
