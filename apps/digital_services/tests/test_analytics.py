"""
Tests for the Analytics MVP tracking (PageEvent) — views/shares of public digital-service pages.

Groups:
  Group 1: Tracking (writes) — the 5 public views + track-share endpoint
  Group 2: Aggregation (reads) — DigitalAnalyticsView
"""
import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.digital_services.models import (
    CVDocument,
    LandingTemplate,
    PageEvent,
    PortfolioItem,
    PublicProfile,
)
from apps.tenants.models import Tenant

User = get_user_model()

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

BASE_URL = '/api/v1/app/digital/'
PUBLIC_URL = '/api/v1/public/'


def _create_tenant(slug, plan='professional'):
    return Tenant.objects.create(name=slug.capitalize(), slug=slug, subdomain=slug, plan=plan)


def _create_superuser(tenant, email):
    user = User.objects.create_user(
        email=email, name='Test User', password='x', tenant=tenant
    )
    user.is_superuser = True
    user.save(update_fields=['is_superuser'])
    return user


def _make_profile(user, username='jsmith', is_public=True):
    return PublicProfile.objects.create(
        user=user,
        username=username,
        display_name='John Smith',
        is_public=is_public,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Group 1: Tracking (writes)
# ══════════════════════════════════════════════════════════════════════════════

@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestPageViewTracking(APITestCase):

    def setUp(self):
        cache.clear()
        self.tenant = _create_tenant('analytics-corp')
        self.user = _create_superuser(self.tenant, 'u@analytics.com')

    # ── Test 1 ───────────────────────────────────────────────────────────────

    def test_visiting_tarjeta_creates_view_event(self):
        """GET /public/profiles/<username>/ creates exactly 1 PageEvent(service='tarjeta', event_type='view')."""
        profile = _make_profile(self.user, username='tarjetauser')
        response = self.client.get(f'{PUBLIC_URL}profiles/tarjetauser/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        events = PageEvent.objects.filter(profile=profile)
        self.assertEqual(events.count(), 1)
        self.assertEqual(events.first().service, 'tarjeta')
        self.assertEqual(events.first().event_type, PageEvent.EVENT_VIEW)

    def test_visiting_landing_creates_view_event(self):
        """GET /public/landing/<username>/ creates exactly 1 PageEvent(service='landing')."""
        profile = _make_profile(self.user, username='landinguser')
        LandingTemplate.objects.create(profile=profile)
        response = self.client.get(f'{PUBLIC_URL}landing/landinguser/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        events = PageEvent.objects.filter(profile=profile, service='landing')
        self.assertEqual(events.count(), 1)

    def test_visiting_portafolio_creates_view_event(self):
        """GET /public/portafolio/<username>/ creates exactly 1 PageEvent(service='portafolio')."""
        profile = _make_profile(self.user, username='portuser')
        response = self.client.get(f'{PUBLIC_URL}portafolio/portuser/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        events = PageEvent.objects.filter(profile=profile, service='portafolio')
        self.assertEqual(events.count(), 1)

    def test_visiting_portfolio_item_creates_view_event(self):
        """GET /public/portafolio/<username>/<slug>/ creates exactly 1 PageEvent(service='portafolio')."""
        profile = _make_profile(self.user, username='itemuser')
        PortfolioItem.objects.create(
            profile=profile, title='Demo', slug='demo',
            description_short='Desc', project_date=datetime.date(2024, 1, 15),
        )
        response = self.client.get(f'{PUBLIC_URL}portafolio/itemuser/demo/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        events = PageEvent.objects.filter(profile=profile, service='portafolio')
        self.assertEqual(events.count(), 1)

    def test_visiting_cv_creates_view_event(self):
        """GET /public/cv/<username>/ creates exactly 1 PageEvent(service='cv')."""
        profile = _make_profile(self.user, username='cvuser')
        CVDocument.objects.create(profile=profile, professional_summary='Dev.')
        response = self.client.get(f'{PUBLIC_URL}cv/cvuser/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        events = PageEvent.objects.filter(profile=profile, service='cv')
        self.assertEqual(events.count(), 1)

    # ── Test 2 ───────────────────────────────────────────────────────────────

    def test_visiting_nonexistent_username_creates_no_event(self):
        """GET /public/profiles/<username>/ with unknown username returns 404, no PageEvent."""
        response = self.client.get(f'{PUBLIC_URL}profiles/doesnotexist/')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(PageEvent.objects.count(), 0)

    def test_visiting_private_profile_creates_no_event(self):
        """GET /public/profiles/<username>/ with is_public=False returns 404, no PageEvent."""
        _make_profile(self.user, username='privateuser', is_public=False)
        response = self.client.get(f'{PUBLIC_URL}profiles/privateuser/')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(PageEvent.objects.count(), 0)

    # ── Test 3/4/5 ───────────────────────────────────────────────────────────

    def test_same_ip_and_ua_same_day_produces_same_session_hash(self):
        """Two requests with the same IP+UA on the same day produce the same session_hash."""
        profile = _make_profile(self.user, username='samehashuser')
        headers = {'REMOTE_ADDR': '1.2.3.4', 'HTTP_USER_AGENT': 'TestAgent/1.0'}
        self.client.get(f'{PUBLIC_URL}profiles/samehashuser/', **headers)
        self.client.get(f'{PUBLIC_URL}profiles/samehashuser/', **headers)
        hashes = list(PageEvent.objects.filter(profile=profile).values_list('session_hash', flat=True))
        self.assertEqual(len(hashes), 2)
        self.assertEqual(hashes[0], hashes[1])

    def test_same_ip_and_ua_different_day_produces_different_session_hash(self):
        """Same IP+UA on two different dates produces two different session_hash values."""
        profile = _make_profile(self.user, username='diffdayuser')
        headers = {'REMOTE_ADDR': '1.2.3.4', 'HTTP_USER_AGENT': 'TestAgent/1.0'}
        day1 = timezone.now()
        day2 = day1 + datetime.timedelta(days=1)
        with patch('apps.digital_services.analytics.timezone.now', return_value=day1):
            self.client.get(f'{PUBLIC_URL}profiles/diffdayuser/', **headers)
        with patch('apps.digital_services.analytics.timezone.now', return_value=day2):
            self.client.get(f'{PUBLIC_URL}profiles/diffdayuser/', **headers)
        hashes = list(PageEvent.objects.filter(profile=profile).values_list('session_hash', flat=True))
        self.assertEqual(len(hashes), 2)
        self.assertNotEqual(hashes[0], hashes[1])

    def test_different_ip_produces_different_session_hash(self):
        """Different IP (same UA, same day) produces a different session_hash."""
        profile = _make_profile(self.user, username='diffipuser')
        self.client.get(
            f'{PUBLIC_URL}profiles/diffipuser/',
            REMOTE_ADDR='1.2.3.4', HTTP_USER_AGENT='TestAgent/1.0',
        )
        self.client.get(
            f'{PUBLIC_URL}profiles/diffipuser/',
            REMOTE_ADDR='5.6.7.8', HTTP_USER_AGENT='TestAgent/1.0',
        )
        hashes = list(PageEvent.objects.filter(profile=profile).values_list('session_hash', flat=True))
        self.assertNotEqual(hashes[0], hashes[1])

    # ── Test 6 ───────────────────────────────────────────────────────────────

    def test_referrer_normalized_to_hostname(self):
        """A Referer header is normalized to just its hostname (query/path stripped)."""
        profile = _make_profile(self.user, username='refuser')
        self.client.get(
            f'{PUBLIC_URL}profiles/refuser/',
            HTTP_REFERER='https://instagram.com/p/xyz?utm_source=bio',
        )
        event = PageEvent.objects.get(profile=profile)
        self.assertEqual(event.referrer, 'instagram.com')

    def test_missing_referrer_does_not_break(self):
        """No Referer header → referrer field is empty, request still succeeds."""
        profile = _make_profile(self.user, username='norefuser')
        response = self.client.get(f'{PUBLIC_URL}profiles/norefuser/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        event = PageEvent.objects.get(profile=profile)
        self.assertEqual(event.referrer, '')

    # ── Test 7 ───────────────────────────────────────────────────────────────

    def test_track_share_valid_service_creates_share_event(self):
        """POST /public/track-share/<username>/ with a valid service creates a share PageEvent."""
        profile = _make_profile(self.user, username='shareuser')
        response = self.client.post(
            f'{PUBLIC_URL}track-share/shareuser/', {'service': 'tarjeta'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        event = PageEvent.objects.get(profile=profile, event_type=PageEvent.EVENT_SHARE)
        self.assertEqual(event.service, 'tarjeta')

    def test_track_share_invalid_service_returns_400(self):
        """POST /public/track-share/<username>/ with an invalid service returns 400."""
        _make_profile(self.user, username='badshareuser')
        response = self.client.post(
            f'{PUBLIC_URL}track-share/badshareuser/', {'service': 'not-a-service'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_track_share_unknown_username_returns_404(self):
        """POST /public/track-share/<username>/ with unknown/private username returns 404."""
        response = self.client.post(
            f'{PUBLIC_URL}track-share/doesnotexist/', {'service': 'tarjeta'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


# ══════════════════════════════════════════════════════════════════════════════
# Group 2: Aggregation (reads) — DigitalAnalyticsView
# ══════════════════════════════════════════════════════════════════════════════

@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestDigitalAnalyticsView(APITestCase):

    def setUp(self):
        cache.clear()
        self.tenant = _create_tenant('agg-corp', plan='professional')
        self.user = _create_superuser(self.tenant, 'u@agg.com')
        self.client.force_authenticate(user=self.user)
        self.slug = {'HTTP_X_TENANT_SLUG': 'agg-corp'}
        self.profile = _make_profile(self.user, username='agguser')

    def _seed_view(self, service, days_ago, session_hash='h1', referrer=''):
        # created_at is auto_now_add — .create() ignores an explicit value, so backdate via .update().
        event = PageEvent.objects.create(
            profile=self.profile, service=service, event_type=PageEvent.EVENT_VIEW,
            session_hash=session_hash, referrer=referrer,
        )
        PageEvent.objects.filter(pk=event.pk).update(
            created_at=timezone.now() - datetime.timedelta(days=days_ago),
        )

    # ── Test 8 ───────────────────────────────────────────────────────────────

    def test_aggregation_totals_and_referrers(self):
        """Seeded multi-day events produce correct total/unique/shares/data/referrers."""
        self._seed_view('tarjeta', 1, session_hash='h1', referrer='google.com')
        self._seed_view('tarjeta', 1, session_hash='h2', referrer='google.com')
        self._seed_view('tarjeta', 2, session_hash='h1', referrer='instagram.com')
        PageEvent.objects.create(
            profile=self.profile, service='tarjeta', event_type=PageEvent.EVENT_SHARE,
        )
        response = self.client.get(f'{BASE_URL}analytics/tarjeta/', **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        analytics = response.json()['analytics']
        self.assertEqual(analytics['total_views'], 3)
        self.assertEqual(analytics['unique_views'], 2)  # h1, h2
        self.assertEqual(analytics['shares'], 1)
        sources = {r['source']: r['visits'] for r in analytics['referrers']}
        self.assertEqual(sources['google.com'], 2)
        self.assertEqual(sources['instagram.com'], 1)
        self.assertEqual(len(analytics['data']), 2)  # 2 distinct days

    # ── Test 9 ───────────────────────────────────────────────────────────────

    def test_aggregation_isolated_between_profiles(self):
        """Events belonging to a different profile never leak into this profile's metrics."""
        other_tenant = _create_tenant('other-corp', plan='professional')
        other_user = _create_superuser(other_tenant, 'u@other.com')
        other_profile = _make_profile(other_user, username='otheruser')
        PageEvent.objects.create(
            profile=other_profile, service='tarjeta', event_type=PageEvent.EVENT_VIEW,
        )
        self._seed_view('tarjeta', 1)
        response = self.client.get(f'{BASE_URL}analytics/tarjeta/', **self.slug)
        self.assertEqual(response.json()['analytics']['total_views'], 1)

    # ── Test 10 ──────────────────────────────────────────────────────────────

    def test_aggregation_filters_by_service(self):
        """Requesting analytics for one service only counts that service's events."""
        self._seed_view('tarjeta', 1)
        self._seed_view('landing', 1)
        self._seed_view('cv', 1)
        response = self.client.get(f'{BASE_URL}analytics/landing/', **self.slug)
        self.assertEqual(response.json()['analytics']['total_views'], 1)
        self.assertEqual(response.json()['analytics']['service'], 'landing')

    # ── Test 11 ──────────────────────────────────────────────────────────────

    def test_change_percent_computed_against_previous_period(self):
        """change_percent compares current period vs. the immediately preceding one."""
        # Previous period (days 31-60 ago): 2 views. Current period (last 30 days): 4 views.
        for _ in range(2):
            self._seed_view('tarjeta', 45)
        for _ in range(4):
            self._seed_view('tarjeta', 5)
        response = self.client.get(f'{BASE_URL}analytics/tarjeta/', **self.slug)
        self.assertEqual(response.json()['analytics']['change_percent'], 100.0)

    def test_change_percent_is_none_when_previous_period_empty(self):
        """change_percent is None (not a crash) when the previous period has zero views."""
        self._seed_view('tarjeta', 5)
        response = self.client.get(f'{BASE_URL}analytics/tarjeta/', **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.json()['analytics']['change_percent'])

    # ── Test 12 ──────────────────────────────────────────────────────────────

    def test_days_clamped_to_plan_limit(self):
        """A starter-plan user (digital_analytics_days=7) requesting days=365 gets results
        clamped to 7 days — an event 10 days old must NOT be counted."""
        starter_tenant = _create_tenant('clamp-corp', plan='starter')
        starter_user = _create_superuser(starter_tenant, 'u@clamp.com')
        starter_profile = _make_profile(starter_user, username='clampuser')
        old_event = PageEvent.objects.create(
            profile=starter_profile, service='tarjeta', event_type=PageEvent.EVENT_VIEW,
        )
        PageEvent.objects.filter(pk=old_event.pk).update(
            created_at=timezone.now() - datetime.timedelta(days=10),
        )
        recent_event = PageEvent.objects.create(
            profile=starter_profile, service='tarjeta', event_type=PageEvent.EVENT_VIEW,
        )
        PageEvent.objects.filter(pk=recent_event.pk).update(
            created_at=timezone.now() - datetime.timedelta(days=3),
        )
        self.client.force_authenticate(user=starter_user)
        response = self.client.get(
            f'{BASE_URL}analytics/tarjeta/', {'days': 365},
            **{'HTTP_X_TENANT_SLUG': 'clamp-corp'},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['analytics']['total_views'], 1)
