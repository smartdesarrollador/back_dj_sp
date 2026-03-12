"""Tests for the notifications app (admin + hub endpoints + signals)."""
import uuid

from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from apps.notifications.models import Notification
from apps.tenants.models import Tenant

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']


# ─── Helpers ──────────────────────────────────────────────────────────────────

def make_tenant(slug: str | None = None) -> Tenant:
    slug = slug or f'tenant-{uuid.uuid4().hex[:8]}'
    return Tenant.objects.create(name=slug, slug=slug, subdomain=slug)


def make_user(tenant: Tenant, email: str | None = None):
    from apps.auth_app.models import User
    email = email or f'user-{uuid.uuid4().hex[:8]}@example.com'
    return User.objects.create_user(
        email=email,
        name='Test User',
        password='testpass123',
        tenant=tenant,
    )


def slug_header(slug: str) -> dict:
    return {'HTTP_X_TENANT_SLUG': slug}


def make_notification(tenant: Tenant, category: str = 'system', read: bool = False) -> Notification:
    return Notification.objects.create(
        tenant=tenant,
        category=category,
        title=f'Test {category}',
        message='Test message',
        icon='Bell',
        read=read,
    )


# ─── Admin notification endpoints ─────────────────────────────────────────────

@override_settings(PASSWORD_HASHERS=_FAST_HASHERS)
class TestAdminNotificationEndpoints(TestCase):
    def setUp(self):
        self.tenant = make_tenant()
        self.user = make_user(self.tenant)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_admin_list_returns_notifications(self):
        make_notification(self.tenant, 'billing')
        make_notification(self.tenant, 'security')
        resp = self.client.get(
            '/api/v1/admin/notifications/',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data['notifications']), 2)
        self.assertIn('pagination', resp.data)

    def test_admin_list_requires_auth(self):
        client = APIClient()
        resp = client.get(
            '/api/v1/admin/notifications/',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_admin_list_tenant_isolation(self):
        other_tenant = make_tenant()
        make_notification(other_tenant, 'billing')
        make_notification(self.tenant, 'users')

        resp = self.client.get(
            '/api/v1/admin/notifications/',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data['notifications']), 1)
        self.assertEqual(resp.data['notifications'][0]['category'], 'users')

    def test_admin_list_excludes_services_category(self):
        make_notification(self.tenant, 'services')
        make_notification(self.tenant, 'billing')

        resp = self.client.get(
            '/api/v1/admin/notifications/',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        categories = [n['category'] for n in resp.data['notifications']]
        self.assertNotIn('services', categories)
        self.assertIn('billing', categories)

    def test_admin_mark_read(self):
        notif = make_notification(self.tenant, 'billing', read=False)
        resp = self.client.post(
            f'/api/v1/admin/notifications/{notif.id}/read/',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        notif.refresh_from_db()
        self.assertTrue(notif.read)

    def test_admin_mark_read_other_tenant_returns_404(self):
        other_tenant = make_tenant()
        notif = make_notification(other_tenant, 'billing')

        resp = self.client.post(
            f'/api/v1/admin/notifications/{notif.id}/read/',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_admin_mark_all_read(self):
        make_notification(self.tenant, 'billing', read=False)
        make_notification(self.tenant, 'security', read=False)
        make_notification(self.tenant, 'system', read=True)

        resp = self.client.post(
            '/api/v1/admin/notifications/read-all/',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['updated_count'], 2)
        self.assertEqual(Notification.objects.filter(tenant=self.tenant, read=False).count(), 0)

    def test_admin_mark_read_not_found(self):
        fake_id = uuid.uuid4()
        resp = self.client.post(
            f'/api/v1/admin/notifications/{fake_id}/read/',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


# ─── Hub notification endpoints ───────────────────────────────────────────────

@override_settings(PASSWORD_HASHERS=_FAST_HASHERS)
class TestHubNotificationEndpoints(TestCase):
    def setUp(self):
        self.tenant = make_tenant()
        self.user = make_user(self.tenant)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_hub_list_shows_hub_categories(self):
        for cat in ['billing', 'security', 'services', 'system']:
            make_notification(self.tenant, cat)

        resp = self.client.get(
            '/api/v1/app/notifications/',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        categories = {n['category'] for n in resp.data['notifications']}
        self.assertSetEqual(categories, {'billing', 'security', 'services', 'system'})

    def test_hub_list_excludes_users_roles(self):
        make_notification(self.tenant, 'users')
        make_notification(self.tenant, 'roles')
        make_notification(self.tenant, 'billing')

        resp = self.client.get(
            '/api/v1/app/notifications/',
            **slug_header(self.tenant.slug),
        )
        categories = [n['category'] for n in resp.data['notifications']]
        self.assertNotIn('users', categories)
        self.assertNotIn('roles', categories)
        self.assertIn('billing', categories)

    def test_hub_list_requires_auth(self):
        client = APIClient()
        resp = client.get(
            '/api/v1/app/notifications/',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_hub_list_tenant_isolation(self):
        other_tenant = make_tenant()
        make_notification(other_tenant, 'billing')
        make_notification(self.tenant, 'services')

        resp = self.client.get(
            '/api/v1/app/notifications/',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(len(resp.data['notifications']), 1)
        self.assertEqual(resp.data['notifications'][0]['category'], 'services')

    def test_hub_list_pagination(self):
        for i in range(25):
            Notification.objects.create(
                tenant=self.tenant,
                category='billing',
                title=f'Notif {i}',
            )

        resp1 = self.client.get(
            '/api/v1/app/notifications/?page=1',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp1.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp1.data['notifications']), 20)
        self.assertEqual(resp1.data['pagination']['total'], 25)

        resp2 = self.client.get(
            '/api/v1/app/notifications/?page=2',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(len(resp2.data['notifications']), 5)

    def test_hub_mark_read(self):
        notif = make_notification(self.tenant, 'services', read=False)
        resp = self.client.post(
            f'/api/v1/app/notifications/{notif.id}/read/',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        notif.refresh_from_db()
        self.assertTrue(notif.read)

    def test_hub_mark_all_read(self):
        make_notification(self.tenant, 'billing', read=False)
        make_notification(self.tenant, 'services', read=False)

        resp = self.client.post(
            '/api/v1/app/notifications/read-all/',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['updated_count'], 2)

    def test_hub_mark_read_not_found(self):
        fake_id = uuid.uuid4()
        resp = self.client.post(
            f'/api/v1/app/notifications/{fake_id}/read/',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


# ─── Signal tests ─────────────────────────────────────────────────────────────

@override_settings(PASSWORD_HASHERS=_FAST_HASHERS)
class TestNotificationSignals(TestCase):
    def setUp(self):
        self.tenant = make_tenant()

    def test_invoice_paid_creates_billing_notification(self):
        from apps.subscriptions.models import Invoice
        Invoice.objects.create(
            tenant=self.tenant,
            stripe_invoice_id=f'inv_{uuid.uuid4().hex}',
            amount_cents=1999,
            status='paid',
        )
        # post_save fired on create with status='paid' → signal creates billing notification
        count = Notification.objects.filter(tenant=self.tenant, category='billing').count()
        self.assertGreaterEqual(count, 1)
        notif = Notification.objects.get(tenant=self.tenant, category='billing')
        self.assertIn('19.99', notif.title)

    def test_invoice_paid_idempotent(self):
        from apps.subscriptions.models import Invoice
        Invoice.objects.create(
            tenant=self.tenant,
            stripe_invoice_id=f'inv_{uuid.uuid4().hex}',
            amount_cents=500,
            status='paid',
        )
        Invoice.objects.create(
            tenant=self.tenant,
            stripe_invoice_id=f'inv_{uuid.uuid4().hex}',
            amount_cents=500,
            status='paid',
        )
        # get_or_create uses (tenant, category, title) as key — same title => only 1
        count = Notification.objects.filter(
            tenant=self.tenant,
            category='billing',
            title='Nueva factura: $5.00',
        ).count()
        self.assertEqual(count, 1)

    def test_tenant_service_suspended_creates_services_notification(self):
        from apps.services.models import Service, TenantService
        service = Service.objects.create(
            slug='workspace',
            name='Workspace',
            icon='Layers',
            url_template='https://workspace.example.com',
        )
        ts = TenantService.objects.create(
            tenant=self.tenant,
            service=service,
            status='active',
        )
        # Suspend → triggers signal
        ts.status = 'suspended'
        ts.save()

        notif = Notification.objects.get(tenant=self.tenant, category='services')
        self.assertIn('Workspace', notif.title)
        self.assertIn('suspendido', notif.title)
