"""
Tests for the Chat module — conversations.
Covers: create direct (get-or-create), create group, list, membership isolation.
"""
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.chat.models import Conversation, ConversationMember
from apps.chat.tests.conftest_helpers import (
    FAST_HASHERS,
    LOCMEM_CACHE,
    create_tenant,
    create_user,
)

BASE = '/api/v1/app/chat/'


@override_settings(PASSWORD_HASHERS=FAST_HASHERS, CACHES=LOCMEM_CACHE)
class TestConversations(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = create_tenant('chat-corp')
        self.alice = create_user(self.tenant, 'alice@chat.com', 'Alice Smith')
        self.bob = create_user(self.tenant, 'bob@chat.com', 'Bob Jones')
        self.carol = create_user(self.tenant, 'carol@chat.com', 'Carol Lee')
        self.headers = {'HTTP_X_TENANT_SLUG': 'chat-corp'}

    def _auth(self, user):
        self.client.force_authenticate(user=user)

    # ── Direct ────────────────────────────────────────────────────────────────

    def test_create_direct_conversation(self):
        self._auth(self.alice)
        res = self.client.post(
            f'{BASE}conversations/',
            {'type': 'direct', 'member_ids': [str(self.bob.id)]},
            format='json', **self.headers,
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Conversation.objects.count(), 1)
        self.assertEqual(ConversationMember.objects.count(), 2)

    def test_direct_is_get_or_create(self):
        self._auth(self.alice)
        payload = {'type': 'direct', 'member_ids': [str(self.bob.id)]}
        first = self.client.post(f'{BASE}conversations/', payload, format='json', **self.headers)
        second = self.client.post(f'{BASE}conversations/', payload, format='json', **self.headers)
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(Conversation.objects.filter(type='direct').count(), 1)
        self.assertEqual(first.json()['id'], second.json()['id'])

    # ── Group ─────────────────────────────────────────────────────────────────

    def test_create_group_conversation(self):
        self._auth(self.alice)
        res = self.client.post(
            f'{BASE}conversations/',
            {'type': 'group', 'name': 'Equipo', 'member_ids': [str(self.bob.id), str(self.carol.id)]},
            format='json', **self.headers,
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.json()['type'], 'group')
        self.assertEqual(res.json()['member_count'], 3)

    def test_group_requires_name(self):
        self._auth(self.alice)
        res = self.client.post(
            f'{BASE}conversations/',
            {'type': 'group', 'member_ids': [str(self.bob.id)]},
            format='json', **self.headers,
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    # ── List & isolation ────────────────────────────────────────────────────

    def test_list_only_my_conversations(self):
        # Alice ↔ Bob
        self._auth(self.alice)
        self.client.post(
            f'{BASE}conversations/',
            {'type': 'direct', 'member_ids': [str(self.bob.id)]},
            format='json', **self.headers,
        )
        # Carol should see none of it.
        self._auth(self.carol)
        res = self.client.get(f'{BASE}conversations/', **self.headers)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.json()['count'], 0)

    def test_non_member_cannot_view_detail(self):
        self._auth(self.alice)
        created = self.client.post(
            f'{BASE}conversations/',
            {'type': 'direct', 'member_ids': [str(self.bob.id)]},
            format='json', **self.headers,
        ).json()
        self._auth(self.carol)
        res = self.client.get(f'{BASE}conversations/{created["id"]}/', **self.headers)
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_rename_group(self):
        self._auth(self.alice)
        created = self.client.post(
            f'{BASE}conversations/',
            {'type': 'group', 'name': 'Old', 'member_ids': [str(self.bob.id)]},
            format='json', **self.headers,
        ).json()
        res = self.client.patch(
            f'{BASE}conversations/{created["id"]}/',
            {'name': 'New Name'}, format='json', **self.headers,
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.json()['name'], 'New Name')
