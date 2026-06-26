"""
Tests for the Chat module — messages.
Covers: send, list, mark read, unread_count, membership isolation.
"""
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.chat.models import Conversation, ConversationMember, Message
from apps.chat.tests.conftest_helpers import (
    FAST_HASHERS,
    LOCMEM_CACHE,
    create_tenant,
    create_user,
)

BASE = '/api/v1/app/chat/'


@override_settings(PASSWORD_HASHERS=FAST_HASHERS, CACHES=LOCMEM_CACHE)
class TestMessages(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = create_tenant('msg-corp')
        self.alice = create_user(self.tenant, 'alice@msg.com', 'Alice Smith')
        self.bob = create_user(self.tenant, 'bob@msg.com', 'Bob Jones')
        self.carol = create_user(self.tenant, 'carol@msg.com', 'Carol Lee')
        self.headers = {'HTTP_X_TENANT_SLUG': 'msg-corp'}
        self.conv = Conversation.objects.create(tenant=self.tenant, type='direct', created_by=self.alice)
        ConversationMember.objects.create(conversation=self.conv, user=self.alice, role='owner')
        ConversationMember.objects.create(conversation=self.conv, user=self.bob, role='member')

    def _auth(self, user):
        self.client.force_authenticate(user=user)

    def test_send_message(self):
        self._auth(self.alice)
        res = self.client.post(
            f'{BASE}messages/',
            {'conversation': str(self.conv.id), 'content': 'Hola Bob'},
            format='json', **self.headers,
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.json()['content'], 'Hola Bob')
        self.assertTrue(res.json()['is_mine'])
        self.assertEqual(Message.objects.count(), 1)

    def test_send_empty_message_rejected(self):
        self._auth(self.alice)
        res = self.client.post(
            f'{BASE}messages/',
            {'conversation': str(self.conv.id), 'content': '   '},
            format='json', **self.headers,
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_non_member_cannot_send(self):
        self._auth(self.carol)
        res = self.client.post(
            f'{BASE}messages/',
            {'conversation': str(self.conv.id), 'content': 'intruso'},
            format='json', **self.headers,
        )
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_non_member_cannot_list(self):
        self._auth(self.carol)
        res = self.client.get(f'{BASE}messages/?conversation={self.conv.id}', **self.headers)
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_list_messages_chronological(self):
        Message.objects.create(conversation=self.conv, sender=self.alice, content='m1')
        Message.objects.create(conversation=self.conv, sender=self.bob, content='m2')
        self._auth(self.alice)
        res = self.client.get(f'{BASE}messages/?conversation={self.conv.id}', **self.headers)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        contents = [m['content'] for m in res.json()['results']]
        self.assertEqual(contents, ['m1', 'm2'])

    def test_unread_count_and_mark_read(self):
        # Bob sends two messages; Alice has 2 unread.
        Message.objects.create(conversation=self.conv, sender=self.bob, content='u1')
        Message.objects.create(conversation=self.conv, sender=self.bob, content='u2')
        self._auth(self.alice)
        res = self.client.get(f'{BASE}conversations/', **self.headers)
        conv_data = res.json()['results'][0]
        self.assertEqual(conv_data['unread_count'], 2)

        self.client.post(f'{BASE}conversations/{self.conv.id}/read/', **self.headers)
        res2 = self.client.get(f'{BASE}conversations/', **self.headers)
        self.assertEqual(res2.json()['results'][0]['unread_count'], 0)

    def test_reply_to_must_belong_to_conversation(self):
        other = Conversation.objects.create(tenant=self.tenant, type='direct', created_by=self.alice)
        ConversationMember.objects.create(conversation=other, user=self.alice, role='owner')
        foreign = Message.objects.create(conversation=other, sender=self.alice, content='foreign')
        self._auth(self.alice)
        res = self.client.post(
            f'{BASE}messages/',
            {'conversation': str(self.conv.id), 'content': 'reply', 'reply_to': str(foreign.id)},
            format='json', **self.headers,
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
