"""
Tests for the Chat module — onboarding of unregistered emails (Phase 3).
Inviting an email with no account creates a pending email invite; registering
that email links the connection via the post_save signal.
"""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.chat.models import ChatConnection
from apps.chat.tests.conftest_helpers import (
    FAST_HASHERS,
    LOCMEM_CACHE,
    create_tenant,
    create_user,
)

User = get_user_model()
BASE = '/api/v1/app/chat/'


@override_settings(PASSWORD_HASHERS=FAST_HASHERS, CACHES=LOCMEM_CACHE)
class TestOnboarding(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant_x = create_tenant('onb-x')
        self.tenant_y = create_tenant('onb-y')
        self.alice = create_user(self.tenant_x, 'alice@onb-x.com', 'Alice Smith')
        self.headers = {'HTTP_X_TENANT_SLUG': 'onb-x'}
        self.client.force_authenticate(user=self.alice)

    def test_invite_unregistered_creates_pending_email_invite(self):
        res = self.client.post(
            f'{BASE}connections/', {'email': 'ghost@onb-y.com'}, format='json', **self.headers
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.json()['status'], 'pending')
        conn = ChatConnection.objects.get()
        self.assertIsNone(conn.addressee_id)
        self.assertEqual(conn.invited_email, 'ghost@onb-y.com')

    def test_duplicate_email_invite_idempotent(self):
        first = self.client.post(f'{BASE}connections/', {'email': 'ghost@onb-y.com'}, format='json', **self.headers)
        second = self.client.post(f'{BASE}connections/', {'email': 'ghost@onb-y.com'}, format='json', **self.headers)
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(ChatConnection.objects.count(), 1)

    def test_registration_links_pending_invite(self):
        self.client.post(f'{BASE}connections/', {'email': 'ghost@onb-y.com'}, format='json', **self.headers)
        # The signal links the invite when the user is created.
        ghost = create_user(self.tenant_y, 'ghost@onb-y.com', 'Ghost User')
        conn = ChatConnection.objects.get()
        self.assertEqual(conn.addressee_id, ghost.id)
        self.assertEqual(conn.addressee_tenant_id, self.tenant_y.id)
        self.assertEqual(conn.invited_email, '')
        self.assertEqual(conn.status, 'pending')

    def test_linked_invite_can_be_accepted_and_enables_chat(self):
        self.client.post(f'{BASE}connections/', {'email': 'ghost@onb-y.com'}, format='json', **self.headers)
        ghost = create_user(self.tenant_y, 'ghost@onb-y.com', 'Ghost User')
        conn = ChatConnection.objects.get()
        # Ghost accepts.
        self.client.force_authenticate(user=ghost)
        res = self.client.post(
            f'{BASE}connections/{conn.id}/respond/', {'action': 'accept'},
            format='json', HTTP_X_TENANT_SLUG='onb-y',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        # Alice can now open a cross-tenant direct chat with ghost.
        self.client.force_authenticate(user=self.alice)
        res2 = self.client.post(
            f'{BASE}conversations/',
            {'type': 'direct', 'member_ids': [str(ghost.id)]},
            format='json', **self.headers,
        )
        self.assertEqual(res2.status_code, status.HTTP_201_CREATED)
