"""
Tests for PASO 14 — Analytics/Reports module.
Covers: feature gates (free/starter/professional), summary keys, usage breakdown, trends, export.
"""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.tenants.models import Tenant

User = get_user_model()

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

SUMMARY_URL = '/api/v1/app/reports/summary/'
USAGE_URL = '/api/v1/app/reports/usage/'
TRENDS_URL = '/api/v1/app/reports/trends/'
EXPORT_URL = '/api/v1/app/reports/export/'


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
class TestReportViews(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = _create_tenant('analytics-corp', plan='professional')
        self.user = _create_superuser(self.tenant, 'u@analytics.com')
        self.client.force_authenticate(user=self.user)
        self.slug = {'HTTP_X_TENANT_SLUG': 'analytics-corp'}

    # ── Summary requires analytics feature ───────────────────────────────────

    def test_summary_requires_analytics_feature(self):
        free_tenant = _create_tenant('free-analytics', plan='free')
        free_user = _create_superuser(free_tenant, 'u@free-analytics.com')
        self.client.force_authenticate(user=free_user)
        response = self.client.get(SUMMARY_URL, **{'HTTP_X_TENANT_SLUG': 'free-analytics'})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # ── Summary returns expected metrics keys ─────────────────────────────────

    def test_summary_returns_metrics_keys(self):
        response = self.client.get(SUMMARY_URL + '?period=30', **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertIn('active_users', body)
        self.assertIn('total_users', body)
        self.assertIn('period_days', body)
        self.assertEqual(body['period_days'], 30)

    # ── Usage returns resource breakdown ──────────────────────────────────────

    def test_usage_returns_resource_breakdown(self):
        response = self.client.get(USAGE_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertIn('plan', body)
        self.assertIn('resources', body)
        self.assertIsInstance(body['resources'], list)
        self.assertTrue(len(body['resources']) > 0)
        resource_names = [r['name'] for r in body['resources']]
        self.assertIn('forms', resource_names)

    # ── Trends requires analytics_trends feature ──────────────────────────────

    def test_trends_requires_analytics_trends_feature(self):
        starter_tenant = _create_tenant('starter-analytics', plan='starter')
        starter_user = _create_superuser(starter_tenant, 'u@starter-analytics.com')
        self.client.force_authenticate(user=starter_user)
        response = self.client.get(TRENDS_URL, **{'HTTP_X_TENANT_SLUG': 'starter-analytics'})
        # starter has analytics=True but analytics_trends=False → 403
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # ── Report export requires pdf_export feature ─────────────────────────────

    def test_report_export_requires_pdf_export_feature(self):
        starter_tenant = _create_tenant('starter-export', plan='starter')
        starter_user = _create_superuser(starter_tenant, 'u@starter-export.com')
        self.client.force_authenticate(user=starter_user)
        response = self.client.get(EXPORT_URL, **{'HTTP_X_TENANT_SLUG': 'starter-export'})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
