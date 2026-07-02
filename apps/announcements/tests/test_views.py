"""Tests for the announcements app (admin + public + app endpoints + cache)."""
import uuid
from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.announcements.models import Announcement
from apps.tenants.models import Tenant

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']


# ─── Helpers ──────────────────────────────────────────────────────────────────

def make_tenant(slug: str | None = None) -> Tenant:
    slug = slug or f'tenant-{uuid.uuid4().hex[:8]}'
    return Tenant.objects.create(name=slug, slug=slug, subdomain=slug)


def make_user(tenant: Tenant, email: str | None = None, is_staff: bool = False):
    from apps.auth_app.models import User
    email = email or f'user-{uuid.uuid4().hex[:8]}@example.com'
    return User.objects.create_user(
        email=email,
        name='Test User',
        password='testpass123',
        tenant=tenant,
        is_staff=is_staff,
    )


def slug_header(slug: str) -> dict:
    return {'HTTP_X_TENANT_SLUG': slug}


def make_announcement(
    title: str = 'Promo',
    is_active: bool = True,
    placement: str = 'both',
    starts_at=None,
    ends_at=None,
    priority: int = 0,
) -> Announcement:
    return Announcement.objects.create(
        title=title,
        message='Test message',
        placement=placement,
        is_active=is_active,
        starts_at=starts_at,
        ends_at=ends_at,
        priority=priority,
    )


# ─── Admin announcement endpoints ─────────────────────────────────────────────

@override_settings(PASSWORD_HASHERS=_FAST_HASHERS)
class TestAdminAnnouncementEndpoints(TestCase):
    def setUp(self):
        cache.clear()
        self.tenant = make_tenant()
        self.staff_user = make_user(self.tenant, is_staff=True)
        self.regular_user = make_user(self.tenant, is_staff=False)
        self.client = APIClient()

    def test_list_requires_auth(self):
        resp = self.client.get('/api/v1/admin/announcements/', **slug_header(self.tenant.slug))
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_list_requires_staff(self):
        self.client.force_authenticate(user=self.regular_user)
        resp = self.client.get('/api/v1/admin/announcements/', **slug_header(self.tenant.slug))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_list_returns_all_announcements_for_staff(self):
        make_announcement('Active one', is_active=True)
        make_announcement('Inactive one', is_active=False)
        self.client.force_authenticate(user=self.staff_user)
        resp = self.client.get('/api/v1/admin/announcements/', **slug_header(self.tenant.slug))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 2)

    def test_create_announcement(self):
        self.client.force_authenticate(user=self.staff_user)
        resp = self.client.post(
            '/api/v1/admin/announcements/',
            {
                'title': 'Black Friday',
                'message': '50% off',
                'placement': 'home',
                'is_active': True,
            },
            format='multipart',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Announcement.objects.count(), 1)

    def test_patch_updates_announcement(self):
        ann = make_announcement('Original title')
        self.client.force_authenticate(user=self.staff_user)
        resp = self.client.patch(
            f'/api/v1/admin/announcements/{ann.id}/',
            {'title': 'Updated title', 'is_active': False},
            format='multipart',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ann.refresh_from_db()
        self.assertEqual(ann.title, 'Updated title')
        self.assertFalse(ann.is_active)

    def test_delete_removes_announcement(self):
        ann = make_announcement()
        self.client.force_authenticate(user=self.staff_user)
        resp = self.client.delete(
            f'/api/v1/admin/announcements/{ann.id}/',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Announcement.objects.filter(id=ann.id).exists())


# ─── Public announcement endpoint ─────────────────────────────────────────────

@override_settings(PASSWORD_HASHERS=_FAST_HASHERS)
class TestPublicAnnouncementEndpoint(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def test_returns_204_when_no_active_announcement(self):
        resp = self.client.get('/api/v1/public/announcements/active/?placement=home')
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)

    def test_returns_active_announcement(self):
        make_announcement('Home promo', is_active=True, placement='home')
        resp = self.client.get('/api/v1/public/announcements/active/?placement=home')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['title'], 'Home promo')

    def test_excludes_inactive_announcement(self):
        make_announcement('Inactive', is_active=False, placement='home')
        resp = self.client.get('/api/v1/public/announcements/active/?placement=home')
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)

    def test_excludes_wrong_placement(self):
        make_announcement('Dashboard only', is_active=True, placement='dashboard')
        resp = self.client.get('/api/v1/public/announcements/active/?placement=home')
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)

    def test_includes_placement_both(self):
        make_announcement('Everywhere', is_active=True, placement='both')
        resp = self.client.get('/api/v1/public/announcements/active/?placement=home')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_excludes_future_announcement(self):
        make_announcement(
            'Not started yet',
            is_active=True,
            placement='home',
            starts_at=timezone.now() + timedelta(days=1),
        )
        resp = self.client.get('/api/v1/public/announcements/active/?placement=home')
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)

    def test_excludes_expired_announcement(self):
        make_announcement(
            'Already ended',
            is_active=True,
            placement='home',
            ends_at=timezone.now() - timedelta(days=1),
        )
        resp = self.client.get('/api/v1/public/announcements/active/?placement=home')
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)

    def test_cache_invalidated_on_admin_update(self):
        tenant = make_tenant()
        staff_user = make_user(tenant, is_staff=True)
        ann = make_announcement('Cached promo', is_active=True, placement='home')

        resp = self.client.get('/api/v1/public/announcements/active/?placement=home')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        admin_client = APIClient()
        admin_client.force_authenticate(user=staff_user)
        admin_client.patch(
            f'/api/v1/admin/announcements/{ann.id}/',
            {'is_active': False},
            format='multipart',
            **slug_header(tenant.slug),
        )

        resp = self.client.get('/api/v1/public/announcements/active/?placement=home')
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)


# ─── App (hub) announcement endpoint ──────────────────────────────────────────

@override_settings(PASSWORD_HASHERS=_FAST_HASHERS)
class TestHubAnnouncementEndpoint(TestCase):
    def setUp(self):
        cache.clear()
        self.tenant = make_tenant()
        self.user = make_user(self.tenant)
        self.client = APIClient()

    def test_requires_auth(self):
        resp = self.client.get(
            '/api/v1/app/announcements/active/?placement=dashboard',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_returns_active_announcement_for_authenticated_user(self):
        make_announcement('Dashboard promo', is_active=True, placement='dashboard')
        self.client.force_authenticate(user=self.user)
        resp = self.client.get(
            '/api/v1/app/announcements/active/?placement=dashboard',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['title'], 'Dashboard promo')
