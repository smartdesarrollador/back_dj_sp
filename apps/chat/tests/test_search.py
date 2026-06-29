"""
Tests for the Chat message search endpoint (`GET /api/v1/app/chat/search/`).
Covers: content match, membership isolation, deleted exclusion, min length,
conversation name resolution.
"""
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.chat.models import Conversation, ConversationMember, Message
from apps.chat.tests.conftest_helpers import (
    FAST_HASHERS,
    LOCMEM_CACHE,
    create_tenant,
    create_user,
)

URL = '/api/v1/app/chat/search/'


@override_settings(PASSWORD_HASHERS=FAST_HASHERS, CACHES=LOCMEM_CACHE)
class TestChatSearch(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = create_tenant('chatsearch-corp')
        self.alice = create_user(self.tenant, 'alice@cs.com', 'Alice Smith')
        self.bob = create_user(self.tenant, 'bob@cs.com', 'Bob Jones')
        self.headers = {'HTTP_X_TENANT_SLUG': 'chatsearch-corp'}

        self.conv = Conversation.objects.create(tenant=self.tenant, type='direct', created_by=self.alice)
        ConversationMember.objects.create(conversation=self.conv, user=self.alice, role='owner')
        ConversationMember.objects.create(conversation=self.conv, user=self.bob, role='member')

    def _auth(self, user):
        self.client.force_authenticate(user=user)

    def test_matches_message_content(self):
        Message.objects.create(conversation=self.conv, sender=self.bob, content='vamos a la reunión mañana')
        Message.objects.create(conversation=self.conv, sender=self.alice, content='ok perfecto')
        self._auth(self.alice)
        body = self.client.get(URL, {'q': 'reunión'}, **self.headers).json()
        self.assertEqual(len(body['messages']), 1)
        item = body['messages'][0]
        self.assertIn('reunión', item['snippet'])
        self.assertEqual(item['sender_name'], 'Bob Jones')
        # Direct chat → display name is the other member's name (from Alice's POV).
        self.assertEqual(item['conversation_name'], 'Bob Jones')

    def test_min_length_returns_empty(self):
        Message.objects.create(conversation=self.conv, sender=self.alice, content='a test')
        self._auth(self.alice)
        res = self.client.get(URL, {'q': 'a'}, **self.headers)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.json()['messages'], [])

    def test_excludes_deleted_messages(self):
        Message.objects.create(
            conversation=self.conv, sender=self.alice, content='secreto borrado',
            deleted_at=timezone.now(),
        )
        self._auth(self.alice)
        body = self.client.get(URL, {'q': 'secreto'}, **self.headers).json()
        self.assertEqual(body['messages'], [])

    def test_membership_isolation(self):
        # A conversation Alice is NOT part of.
        carol = create_user(self.tenant, 'carol@cs.com', 'Carol Lee')
        other = Conversation.objects.create(tenant=self.tenant, type='direct', created_by=self.bob)
        ConversationMember.objects.create(conversation=other, user=self.bob, role='owner')
        ConversationMember.objects.create(conversation=other, user=carol, role='member')
        Message.objects.create(conversation=other, sender=self.bob, content='proyecto confidencial')

        self._auth(self.alice)
        body = self.client.get(URL, {'q': 'confidencial'}, **self.headers).json()
        self.assertEqual(body['messages'], [])

    def test_group_conversation_name(self):
        group = Conversation.objects.create(
            tenant=self.tenant, type='group', name='Equipo DevOps', created_by=self.alice
        )
        ConversationMember.objects.create(conversation=group, user=self.alice, role='owner')
        Message.objects.create(conversation=group, sender=self.alice, content='deploy a producción')
        self._auth(self.alice)
        body = self.client.get(URL, {'q': 'deploy'}, **self.headers).json()
        self.assertEqual(len(body['messages']), 1)
        self.assertEqual(body['messages'][0]['conversation_name'], 'Equipo DevOps')
