"""
Tests for PASO 13 — SSL Certs module.
Covers: list, create (status=valid), plan limit (free=0), status expired, status expiring.
"""
import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.ssl_certs.models import SSLCertificate
from apps.tenants.models import Tenant

User = get_user_model()

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

BASE_URL = '/api/v1/app/ssl-certs/'


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
class TestSSLCertViews(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = _create_tenant('ssl-corp')
        self.user = _create_superuser(self.tenant, 'u@ssl.com')
        self.client.force_authenticate(user=self.user)
        self.slug = {'HTTP_X_TENANT_SLUG': 'ssl-corp'}

    # ── List ──────────────────────────────────────────────────────────────────

    def test_list_ssl_certs_empty(self):
        response = self.client.get(BASE_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['ssl_certs'], [])

    # ── Create with status=valid ──────────────────────────────────────────────

    def test_create_ssl_cert_success(self):
        today = timezone.now().date()
        future = today + datetime.timedelta(days=90)
        data = {
            'domain': 'example.com',
            'issuer': "Let's Encrypt",
            'valid_from': str(today),
            'valid_until': str(future),
        }
        response = self.client.post(BASE_URL, data, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        body = response.json()
        self.assertEqual(body['domain'], 'example.com')
        self.assertEqual(body['status'], 'valid')
        self.assertTrue(body['days_until_expiry'] > 30)

    # ── Plan limit (free plan max=0) ──────────────────────────────────────────

    def test_create_ssl_cert_exceeds_plan_limit(self):
        with patch('apps.ssl_certs.views.check_plan_limit') as mock_limit:
            from core.exceptions import PlanLimitExceeded
            mock_limit.side_effect = PlanLimitExceeded()
            response = self.client.post(BASE_URL, {'domain': 'x.com'}, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)

    # ── Status: expired ───────────────────────────────────────────────────────

    def test_ssl_cert_status_expired(self):
        yesterday = timezone.now().date() - datetime.timedelta(days=1)
        cert = SSLCertificate.objects.create(
            tenant=self.tenant,
            user=self.user,
            domain='expired.com',
            valid_until=yesterday,
        )
        self.assertEqual(cert.status, 'expired')
        url = f'{BASE_URL}{cert.pk}/'
        response = self.client.get(url, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['ssl_cert']['status'], 'expired')

    # ── Status: expiring ──────────────────────────────────────────────────────

    def test_ssl_cert_status_expiring(self):
        soon = timezone.now().date() + datetime.timedelta(days=15)
        cert = SSLCertificate.objects.create(
            tenant=self.tenant,
            user=self.user,
            domain='expiring.com',
            valid_until=soon,
        )
        self.assertEqual(cert.status, 'expiring')
        url = f'{BASE_URL}{cert.pk}/'
        response = self.client.get(url, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['ssl_cert']['status'], 'expiring')
