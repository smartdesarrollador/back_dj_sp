"""
Tests for PASO 16 — Calendar module.
Covers: list events, create event, invalid dates, date filter, plan limit.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.calendar_app.models import CalendarEvent
from apps.tenants.models import Tenant

User = get_user_model()

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

BASE_URL = '/api/v1/app/calendar/'


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
class TestCalendarViews(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = _create_tenant('cal-corp')
        self.user = _create_superuser(self.tenant, 'u@cal.com')
        self.client.force_authenticate(user=self.user)
        self.slug = {'HTTP_X_TENANT_SLUG': 'cal-corp'}

    # ── List ──────────────────────────────────────────────────────────────────

    def test_list_events_empty(self):
        """GET /calendar/ returns empty list when no events exist."""
        response = self.client.get(BASE_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['events'], [])

    # ── Create ────────────────────────────────────────────────────────────────

    def test_create_event_success(self):
        """POST /calendar/ creates event with valid start/end datetimes."""
        data = {
            'title': 'Team Standup',
            'start_datetime': '2026-03-10T09:00:00Z',
            'end_datetime': '2026-03-10T09:30:00Z',
            'color': 'green',
        }
        response = self.client.post(BASE_URL, data, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        body = response.json()
        self.assertEqual(body['title'], 'Team Standup')
        self.assertEqual(body['color'], 'green')
        self.assertTrue(
            CalendarEvent.objects.filter(tenant=self.tenant, title='Team Standup').exists()
        )

    # ── Date validation ───────────────────────────────────────────────────────

    def test_create_event_invalid_dates(self):
        """POST /calendar/ with end < start returns 400."""
        data = {
            'title': 'Bad Event',
            'start_datetime': '2026-03-10T10:00:00Z',
            'end_datetime': '2026-03-10T09:00:00Z',  # before start
        }
        response = self.client.post(BASE_URL, data, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # ── Date filter ───────────────────────────────────────────────────────────

    def test_list_events_date_filter(self):
        """GET /calendar/?start=&end= filters events by date range."""
        CalendarEvent.objects.create(
            tenant=self.tenant,
            user=self.user,
            title='In Range',
            start_datetime='2026-03-15T10:00:00Z',
            end_datetime='2026-03-15T11:00:00Z',
        )
        CalendarEvent.objects.create(
            tenant=self.tenant,
            user=self.user,
            title='Out of Range',
            start_datetime='2026-04-01T10:00:00Z',
            end_datetime='2026-04-01T11:00:00Z',
        )
        response = self.client.get(
            BASE_URL,
            {'start': '2026-03-01T00:00:00Z', 'end': '2026-03-31T23:59:59Z'},
            **self.slug,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        titles = [e['title'] for e in response.json()['events']]
        self.assertIn('In Range', titles)
        self.assertNotIn('Out of Range', titles)

    # ── Plan limit ────────────────────────────────────────────────────────────

    def test_create_event_exceeds_plan_limit(self):
        """POST /calendar/ raises 402 when plan limit is exceeded."""
        with patch('apps.calendar_app.views.check_plan_limit') as mock_limit:
            from core.exceptions import PlanLimitExceeded
            mock_limit.side_effect = PlanLimitExceeded()
            data = {
                'title': 'Over limit',
                'start_datetime': '2026-03-10T09:00:00Z',
                'end_datetime': '2026-03-10T10:00:00Z',
            }
            response = self.client.post(BASE_URL, data, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)
