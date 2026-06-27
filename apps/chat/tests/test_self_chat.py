"""
Tests for the Chat module — self-chat ("Mensajes guardados").
Covers: get-or-create idempotency, single member, display name, send/list,
convert own message to a note, and isolation between users.
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
class TestSelfChat(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = create_tenant('chat-corp', plan='professional')
        self.alice = create_user(self.tenant, 'alice@chat.com', 'Alice Smith', superuser=True)
        self.bob = create_user(self.tenant, 'bob@chat.com', 'Bob Jones', superuser=True)
        self.headers = {'HTTP_X_TENANT_SLUG': 'chat-corp'}

    def _auth(self, user):
        self.client.force_authenticate(user=user)

    def test_create_self_chat(self):
        self._auth(self.alice)
        res = self.client.post(f'{BASE}conversations/self/', **self.headers)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.json()['type'], 'self')
        self.assertEqual(res.json()['display_name'], 'Mensajes guardados')
        conv = Conversation.objects.get(type='self')
        self.assertEqual(conv.members.count(), 1)
        self.assertEqual(conv.members.first().user_id, self.alice.id)

    def test_self_chat_is_get_or_create(self):
        self._auth(self.alice)
        first = self.client.post(f'{BASE}conversations/self/', **self.headers)
        second = self.client.post(f'{BASE}conversations/self/', **self.headers)
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(Conversation.objects.filter(type='self').count(), 1)
        self.assertEqual(first.json()['id'], second.json()['id'])

    def test_each_user_has_own_self_chat(self):
        self._auth(self.alice)
        alice_self = self.client.post(f'{BASE}conversations/self/', **self.headers).json()
        self._auth(self.bob)
        bob_self = self.client.post(f'{BASE}conversations/self/', **self.headers).json()
        self.assertNotEqual(alice_self['id'], bob_self['id'])
        self.assertEqual(Conversation.objects.filter(type='self').count(), 2)

    def test_send_and_list_in_self_chat(self):
        self._auth(self.alice)
        conv = self.client.post(f'{BASE}conversations/self/', **self.headers).json()
        send = self.client.post(
            f'{BASE}messages/',
            {'conversation': conv['id'], 'content': 'nota para mí'},
            format='json', **self.headers,
        )
        self.assertEqual(send.status_code, status.HTTP_201_CREATED)
        listing = self.client.get(
            f'{BASE}messages/?conversation={conv["id"]}', **self.headers
        )
        self.assertEqual(listing.status_code, status.HTTP_200_OK)
        self.assertEqual(listing.json()['count'], 1)
        self.assertEqual(listing.json()['results'][0]['content'], 'nota para mí')

    def test_other_user_cannot_see_my_self_chat(self):
        self._auth(self.alice)
        conv = self.client.post(f'{BASE}conversations/self/', **self.headers).json()
        self._auth(self.bob)
        res = self.client.get(f'{BASE}conversations/{conv["id"]}/', **self.headers)
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_convert_self_message_to_note(self):
        self._auth(self.alice)
        conv = self.client.post(f'{BASE}conversations/self/', **self.headers).json()
        msg = self.client.post(
            f'{BASE}messages/',
            {'conversation': conv['id'], 'content': 'idea para guardar'},
            format='json', **self.headers,
        ).json()
        res = self.client.post(
            f'{BASE}messages/{msg["id"]}/convert/',
            {'target': 'note'}, format='json', **self.headers,
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.json()['target'], 'note')

    def test_self_chat_appears_in_list(self):
        self._auth(self.alice)
        self.client.post(f'{BASE}conversations/self/', **self.headers)
        res = self.client.get(f'{BASE}conversations/', **self.headers)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self_convs = [c for c in res.json()['results'] if c['type'] == 'self']
        self.assertEqual(len(self_convs), 1)
        self.assertEqual(self_convs[0]['display_name'], 'Mensajes guardados')
