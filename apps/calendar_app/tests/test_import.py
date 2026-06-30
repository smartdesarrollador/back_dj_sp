"""Tests for bulk calendar event import (POST /api/v1/app/calendar/import/)."""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.audit.models import AuditLog
from apps.calendar_app.models import CalendarEvent
from apps.tenants.models import Tenant

User = get_user_model()

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

IMPORT_URL = '/api/v1/app/calendar/import/'


def _create_tenant(slug, plan='professional'):
    return Tenant.objects.create(name=slug.capitalize(), slug=slug, subdomain=slug, plan=plan)


def _create_superuser(tenant, email):
    user = User.objects.create_user(email=email, name='T', password='x', tenant=tenant)
    user.is_superuser = True
    user.save(update_fields=['is_superuser'])
    return user


def _event(title, start, end, **extra):
    return {'title': title, 'start_datetime': start, 'end_datetime': end, **extra}


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestCalendarImport(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = _create_tenant('cal-imp', plan='professional')
        self.user = _create_superuser(self.tenant, 'u@cal-imp.com')
        self.client.force_authenticate(user=self.user)
        self.slug = {'HTTP_X_TENANT_SLUG': 'cal-imp'}

    def test_import_creates_events(self):
        items = [
            _event('Standup', '2026-06-01T09:00:00Z', '2026-06-01T09:30:00Z'),
            _event('Review', '2026-06-02T10:00:00Z', '2026-06-02T11:00:00Z', location='Room 1'),
        ]
        r = self.client.post(IMPORT_URL, {'items': items}, format='json', **self.slug)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.json()['created'], 2)
        self.assertEqual(CalendarEvent.objects.filter(tenant=self.tenant, user=self.user).count(), 2)

    def test_end_before_start_is_an_error_not_an_abort(self):
        items = [
            _event('Valid', '2026-06-01T09:00:00Z', '2026-06-01T10:00:00Z'),
            _event('Backwards', '2026-06-01T10:00:00Z', '2026-06-01T09:00:00Z'),
        ]
        body = self.client.post(IMPORT_URL, {'items': items}, format='json', **self.slug).json()
        self.assertEqual(body['created'], 1)
        self.assertEqual(body['errors'][0]['index'], 1)

    def test_feature_gate_blocks_free_plan(self):
        self.tenant.plan = 'free'
        self.tenant.save(update_fields=['plan'])
        item = _event('X', '2026-06-01T09:00:00Z', '2026-06-01T10:00:00Z')
        r = self.client.post(IMPORT_URL, {'items': [item]}, format='json', **self.slug)
        self.assertIn(r.status_code, (status.HTTP_402_PAYMENT_REQUIRED, status.HTTP_403_FORBIDDEN))

    def test_partial_plan_limit(self):
        self.tenant.plan = 'starter'  # max_calendar_events = 200
        self.tenant.save(update_fields=['plan'])
        items = [
            _event(f'E{i}', '2026-06-01T09:00:00Z', '2026-06-01T10:00:00Z') for i in range(201)
        ]
        body = self.client.post(IMPORT_URL, {'items': items}, format='json', **self.slug).json()
        self.assertEqual(body['created'], 200)
        self.assertEqual(body['skipped'], 1)

    def test_import_is_audited(self):
        item = _event('X', '2026-06-01T09:00:00Z', '2026-06-01T10:00:00Z')
        self.client.post(IMPORT_URL, {'items': [item]}, format='json', **self.slug)
        self.assertTrue(
            AuditLog.objects.filter(action='calendar.import', resource_type='CalendarEvent').exists()
        )
