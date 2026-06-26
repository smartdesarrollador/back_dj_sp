"""
Tests for the Chat module — cross-tenant connections (Phase 2).
Covers: invite by email, respond (accept/reject), eligibility for direct/group
chats across tenants, and isolation regression.
"""
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.chat.models import ChatConnection, Conversation
from apps.chat.tests.conftest_helpers import (
    FAST_HASHERS,
    LOCMEM_CACHE,
    create_tenant,
    create_user,
)

BASE = '/api/v1/app/chat/'


@override_settings(PASSWORD_HASHERS=FAST_HASHERS, CACHES=LOCMEM_CACHE)
class TestConnections(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant_x = create_tenant('empresa-x')
        self.tenant_y = create_tenant('empresa-y')
        self.alice = create_user(self.tenant_x, 'alice@x.com', 'Alice Smith')
        self.bob = create_user(self.tenant_y, 'bob@y.com', 'Bob Jones')
        self.carol = create_user(self.tenant_y, 'carol@y.com', 'Carol Lee')
        self.hx = {'HTTP_X_TENANT_SLUG': 'empresa-x'}
        self.hy = {'HTTP_X_TENANT_SLUG': 'empresa-y'}

    def _auth(self, user):
        self.client.force_authenticate(user=user)

    # ── Invite ────────────────────────────────────────────────────────────────

    def test_invite_registered_user_creates_pending(self):
        self._auth(self.alice)
        res = self.client.post(f'{BASE}connections/', {'email': 'bob@y.com'}, format='json', **self.hx)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.json()['status'], 'pending')
        self.assertEqual(res.json()['direction'], 'outgoing')
        self.assertEqual(res.json()['tenant_name'], 'Empresa-y')

    def test_invite_unregistered_email_creates_pending_invite(self):
        # Phase 3 onboarding: unregistered email → pending email invite (not 404).
        self._auth(self.alice)
        res = self.client.post(f'{BASE}connections/', {'email': 'ghost@nowhere.com'}, format='json', **self.hx)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.json()['status'], 'pending')
        conn = ChatConnection.objects.get(invited_email='ghost@nowhere.com')
        self.assertIsNone(conn.addressee_id)

    def test_invite_self_rejected(self):
        self._auth(self.alice)
        res = self.client.post(f'{BASE}connections/', {'email': 'alice@x.com'}, format='json', **self.hx)
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invite_duplicate_is_idempotent(self):
        self._auth(self.alice)
        first = self.client.post(f'{BASE}connections/', {'email': 'bob@y.com'}, format='json', **self.hx)
        second = self.client.post(f'{BASE}connections/', {'email': 'bob@y.com'}, format='json', **self.hx)
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(ChatConnection.objects.count(), 1)

    # ── Respond ───────────────────────────────────────────────────────────────

    def _invite(self):
        self._auth(self.alice)
        res = self.client.post(f'{BASE}connections/', {'email': 'bob@y.com'}, format='json', **self.hx)
        return res.json()['id']

    def test_addressee_accepts(self):
        conn_id = self._invite()
        self._auth(self.bob)
        res = self.client.post(f'{BASE}connections/{conn_id}/respond/', {'action': 'accept'}, format='json', **self.hy)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.json()['status'], 'accepted')

    def test_non_addressee_cannot_respond(self):
        conn_id = self._invite()
        self._auth(self.carol)  # not the addressee
        res = self.client.post(f'{BASE}connections/{conn_id}/respond/', {'action': 'accept'}, format='json', **self.hy)
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_reject_deletes_connection(self):
        conn_id = self._invite()
        self._auth(self.bob)
        res = self.client.post(f'{BASE}connections/{conn_id}/respond/', {'action': 'reject'}, format='json', **self.hy)
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(ChatConnection.objects.count(), 0)

    def test_list_groups_by_status(self):
        conn_id = self._invite()
        self._auth(self.bob)
        self.client.post(f'{BASE}connections/{conn_id}/respond/', {'action': 'accept'}, format='json', **self.hy)
        res = self.client.get(f'{BASE}connections/', **self.hy)
        self.assertEqual(len(res.json()['accepted']), 1)
        self.assertEqual(res.json()['accepted'][0]['other_user']['email'], 'alice@x.com')

    # ── Cross-tenant chat eligibility ────────────────────────────────────────

    def _accept_alice_bob(self):
        conn_id = self._invite()
        self._auth(self.bob)
        self.client.post(f'{BASE}connections/{conn_id}/respond/', {'action': 'accept'}, format='json', **self.hy)

    def test_direct_cross_tenant_requires_connection(self):
        # No connection yet → cannot start a direct chat with Bob (other tenant).
        self._auth(self.alice)
        res = self.client.post(
            f'{BASE}conversations/',
            {'type': 'direct', 'member_ids': [str(self.bob.id)]},
            format='json', **self.hx,
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_direct_cross_tenant_after_accept(self):
        self._accept_alice_bob()
        self._auth(self.alice)
        res = self.client.post(
            f'{BASE}conversations/',
            {'type': 'direct', 'member_ids': [str(self.bob.id)]},
            format='json', **self.hx,
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        conv = Conversation.objects.get(id=res.json()['id'])
        self.assertIsNone(conv.tenant_id)  # cross-tenant thread has no owning tenant

    def test_group_cross_tenant_mixes_members(self):
        self._accept_alice_bob()
        dave = create_user(self.tenant_x, 'dave@x.com', 'Dave X')  # same tenant as Alice
        self._auth(self.alice)
        res = self.client.post(
            f'{BASE}conversations/',
            {'type': 'group', 'name': 'Mixto', 'member_ids': [str(self.bob.id), str(dave.id)]},
            format='json', **self.hx,
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.json()['member_count'], 3)

    def test_group_rejects_unconnected_other_tenant(self):
        # Carol (tenant Y) is NOT connected to Alice → cannot be added.
        self._auth(self.alice)
        res = self.client.post(
            f'{BASE}conversations/',
            {'type': 'group', 'name': 'Bad', 'member_ids': [str(self.carol.id)]},
            format='json', **self.hx,
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cross_tenant_messaging_and_isolation(self):
        self._accept_alice_bob()
        self._auth(self.alice)
        conv_id = self.client.post(
            f'{BASE}conversations/',
            {'type': 'direct', 'member_ids': [str(self.bob.id)]},
            format='json', **self.hx,
        ).json()['id']
        self.client.post(
            f'{BASE}messages/',
            {'conversation': conv_id, 'content': 'Hola Bob (otra empresa)'},
            format='json', **self.hx,
        )
        # Bob (tenant Y) sees the message.
        self._auth(self.bob)
        res = self.client.get(f'{BASE}messages/?conversation={conv_id}', **self.hy)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.json()['results'][0]['content'], 'Hola Bob (otra empresa)')
        # Carol (tenant Y, not a member) cannot.
        self._auth(self.carol)
        res2 = self.client.get(f'{BASE}messages/?conversation={conv_id}', **self.hy)
        self.assertEqual(res2.status_code, status.HTTP_404_NOT_FOUND)
