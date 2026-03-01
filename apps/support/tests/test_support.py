"""
Tests for PASO 19 — Support Tickets module.

Covers:
  Group 1: Ticket CRUD + scoping (5 tests)
  Group 2: Comments + feature gates + filters (5 tests)
"""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.support.models import SupportTicket, TicketComment
from apps.tenants.models import Tenant

User = get_user_model()

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

BASE_URL = '/api/v1/support/tickets/'


def _create_tenant(slug, plan='professional'):
    return Tenant.objects.create(name=slug.capitalize(), slug=slug, subdomain=slug, plan=plan)


def _create_superuser(tenant, email):
    user = User.objects.create_user(
        email=email, name='Test User', password='x', tenant=tenant
    )
    user.is_superuser = True
    user.save(update_fields=['is_superuser'])
    return user


def _create_regular_user(tenant, email, name='Regular User'):
    return User.objects.create_user(
        email=email, name=name, password='x', tenant=tenant
    )


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestSupportTicketCRUD(APITestCase):
    """Group 1: Ticket CRUD + scoping (5 tests)."""

    def setUp(self):
        cache.clear()
        self.tenant = _create_tenant('support-corp')
        self.user = _create_superuser(self.tenant, 'admin@support.com')
        self.client.force_authenticate(user=self.user)
        self.slug = {'HTTP_X_TENANT_SLUG': 'support-corp'}

    # ── 1. Create ticket success ──────────────────────────────────────────────

    def test_create_ticket_success(self):
        data = {
            'subject': 'Cannot login',
            'description': 'I get 401 when trying to log in.',
            'category': 'access',
            'priority': 'alta',
        }
        response = self.client.post(BASE_URL, data, format='json', **self.slug)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        body = response.json()['ticket']
        self.assertIn('reference', body)
        self.assertTrue(body['reference'].startswith('TKT-'))
        self.assertEqual(len(body['reference']), 12)  # "TKT-" + 8 chars
        self.assertEqual(body['status'], 'open')
        ticket = SupportTicket.objects.get(pk=body['id'])
        self.assertEqual(ticket.client, self.user)

    # ── 2. Client sees only own ticket ────────────────────────────────────────

    def test_client_sees_only_own_ticket(self):
        user_a = _create_regular_user(self.tenant, 'a@support.com', 'User A')
        user_b = _create_regular_user(self.tenant, 'b@support.com', 'User B')

        ticket_a = SupportTicket.objects.create(
            tenant=self.tenant, client=user_a,
            subject='Issue A', description='desc', category='technical',
        )
        SupportTicket.objects.create(
            tenant=self.tenant, client=user_b,
            subject='Issue B', description='desc', category='billing',
        )

        self.client.force_authenticate(user=user_a)
        response = self.client.get(BASE_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        tickets = response.json()['tickets']
        self.assertEqual(len(tickets), 1)
        self.assertEqual(tickets[0]['id'], str(ticket_a.pk))

    # ── 3. Superuser sees all tickets ─────────────────────────────────────────

    def test_superuser_sees_all_tickets(self):
        user_a = _create_regular_user(self.tenant, 'aa@support.com', 'User AA')
        user_b = _create_regular_user(self.tenant, 'bb@support.com', 'User BB')

        SupportTicket.objects.create(
            tenant=self.tenant, client=user_a,
            subject='Ticket 1', description='desc', category='technical',
        )
        SupportTicket.objects.create(
            tenant=self.tenant, client=user_b,
            subject='Ticket 2', description='desc', category='billing',
        )

        # Superuser is already authenticated in setUp
        response = self.client.get(BASE_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        tickets = response.json()['tickets']
        self.assertEqual(len(tickets), 2)

    # ── 4. Close ticket ───────────────────────────────────────────────────────

    def test_close_ticket(self):
        ticket = SupportTicket.objects.create(
            tenant=self.tenant, client=self.user,
            subject='Close me', description='desc', category='other',
        )
        url = f'{BASE_URL}{ticket.pk}/close/'
        response = self.client.post(url, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, 'closed')

    # ── 5. resolved_at set on status resolve ──────────────────────────────────

    def test_resolved_at_set_on_status_resolve(self):
        ticket = SupportTicket.objects.create(
            tenant=self.tenant, client=self.user,
            subject='Resolve me', description='desc', category='technical',
        )
        self.assertIsNone(ticket.resolved_at)
        url = f'{BASE_URL}{ticket.pk}/'
        response = self.client.patch(url, {'status': 'resolved'}, format='json', **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()['ticket']
        self.assertIsNotNone(body['resolved_at'])
        ticket.refresh_from_db()
        self.assertIsNotNone(ticket.resolved_at)


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestSupportCommentsAndFeatures(APITestCase):
    """Group 2: Comments + feature gates + filters (5 tests)."""

    def setUp(self):
        cache.clear()
        self.tenant = _create_tenant('support-pro', plan='professional')
        self.user = _create_superuser(self.tenant, 'sup@pro.com')
        self.client.force_authenticate(user=self.user)
        self.slug = {'HTTP_X_TENANT_SLUG': 'support-pro'}
        self.ticket = SupportTicket.objects.create(
            tenant=self.tenant, client=self.user,
            subject='Main Ticket', description='desc', category='technical',
            status='open',
        )

    # ── 6. Add comment success (agent role for superuser) ─────────────────────

    def test_add_comment_success(self):
        url = f'{BASE_URL}{self.ticket.pk}/comments/'
        response = self.client.post(url, {'message': 'We are looking into it.'}, format='json', **self.slug)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        body = response.json()['comment']
        self.assertEqual(body['role'], 'agent')
        self.assertEqual(body['message'], 'We are looking into it.')
        self.assertEqual(TicketComment.objects.filter(ticket=self.ticket).count(), 1)

    # ── 7. Regular user comment role = client ─────────────────────────────────

    def test_comment_role_client_for_regular_user(self):
        regular = _create_regular_user(self.tenant, 'reg@pro.com', 'Regular')
        self.client.force_authenticate(user=regular)
        url = f'{BASE_URL}{self.ticket.pk}/comments/'
        response = self.client.post(url, {'message': 'Any update?'}, format='json', **self.slug)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        body = response.json()['comment']
        self.assertEqual(body['role'], 'client')

    # ── 8. Filter tickets by status ───────────────────────────────────────────

    def test_filter_tickets_by_status(self):
        SupportTicket.objects.create(
            tenant=self.tenant, client=self.user,
            subject='Resolved ticket', description='desc', category='billing',
            status='resolved',
        )
        # Ticket in setUp is 'open'
        response = self.client.get(f'{BASE_URL}?status=open', **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        tickets = response.json()['tickets']
        self.assertEqual(len(tickets), 1)
        self.assertEqual(tickets[0]['status'], 'open')

    # ── 9. Export requires professional plan ──────────────────────────────────

    def test_support_export_requires_professional(self):
        starter_tenant = _create_tenant('starter-sup', plan='starter')
        starter_user = _create_superuser(starter_tenant, 'sup@starter.com')
        self.client.force_authenticate(user=starter_user)
        response = self.client.get(
            f'{BASE_URL}export/', **{'HTTP_X_TENANT_SLUG': 'starter-sup'}
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # ── 10. Export returns CSV for professional plan ──────────────────────────

    def test_support_export_returns_csv(self):
        response = self.client.get(f'{BASE_URL}export/', **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('text/csv', response.get('Content-Type', ''))
        content = b''.join(response.streaming_content) if hasattr(response, 'streaming_content') else response.content
        self.assertIn(b'reference', content)
        self.assertIn(b'subject', content)
