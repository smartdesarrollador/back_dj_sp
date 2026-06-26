"""
Tests for the Chat module — converting messages to note/contact/snippet.
Covers: each target, tenant isolation (created in converter's tenant),
plan-limit 402, non-member 404.
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
from apps.contacts.models import Contact
from apps.notes.models import Note
from apps.snippets.models import CodeSnippet

BASE = '/api/v1/app/chat/'


@override_settings(PASSWORD_HASHERS=FAST_HASHERS, CACHES=LOCMEM_CACHE)
class TestMessageConvert(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = create_tenant('conv-corp', plan='professional')
        self.alice = create_user(self.tenant, 'alice@conv.com', 'Alice Smith', superuser=True)
        self.bob = create_user(self.tenant, 'bob@conv.com', 'Bob Jones', superuser=True)
        self.headers = {'HTTP_X_TENANT_SLUG': 'conv-corp'}
        self.conv = Conversation.objects.create(tenant=self.tenant, type='direct', created_by=self.alice)
        ConversationMember.objects.create(conversation=self.conv, user=self.alice, role='owner')
        ConversationMember.objects.create(conversation=self.conv, user=self.bob, role='member')
        self.message = Message.objects.create(
            conversation=self.conv, sender=self.bob, content='console.log("hola")'
        )
        self.client.force_authenticate(user=self.alice)

    def _convert(self, target, extra=None):
        return self.client.post(
            f'{BASE}messages/{self.message.id}/convert/',
            {'target': target, **(extra or {})}, format='json', **self.headers,
        )

    def test_convert_to_note(self):
        res = self._convert('note')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        note = Note.objects.get(id=res.json()['id'])
        # Created in the converter's tenant/user, not the sender's context.
        self.assertEqual(note.user_id, self.alice.id)
        self.assertEqual(note.content, 'console.log("hola")')

    def test_convert_to_contact(self):
        res = self._convert('contact')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        contact = Contact.objects.get(id=res.json()['id'])
        self.assertEqual(contact.user_id, self.alice.id)
        self.assertEqual(contact.first_name, 'Bob')
        self.assertEqual(contact.email, 'bob@conv.com')

    def test_convert_to_snippet(self):
        res = self._convert('snippet', {'language': 'javascript'})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        snippet = CodeSnippet.objects.get(id=res.json()['id'])
        self.assertEqual(snippet.user_id, self.alice.id)
        self.assertEqual(snippet.language, 'javascript')
        self.assertEqual(snippet.code, 'console.log("hola")')

    def test_invalid_target(self):
        res = self._convert('bookmark')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_non_member_cannot_convert(self):
        carol = create_user(self.tenant, 'carol@conv.com', 'Carol Lee', superuser=True)
        self.client.force_authenticate(user=carol)
        res = self._convert('note')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_convert_respects_plan_limit(self):
        free_tenant = create_tenant('free-corp', plan='free')  # max_notes = 10
        u = create_user(free_tenant, 'u@free.com', 'Free User', superuser=True)
        other = create_user(free_tenant, 'o@free.com', 'Other User', superuser=True)
        conv = Conversation.objects.create(tenant=free_tenant, type='direct', created_by=u)
        ConversationMember.objects.create(conversation=conv, user=u, role='owner')
        ConversationMember.objects.create(conversation=conv, user=other, role='member')
        msg = Message.objects.create(conversation=conv, sender=other, content='x')
        for i in range(10):
            Note.objects.create(tenant=free_tenant, user=u, title=f'n{i}', content='x')
        self.client.force_authenticate(user=u)
        res = self.client.post(
            f'{BASE}messages/{msg.id}/convert/',
            {'target': 'note'}, format='json', HTTP_X_TENANT_SLUG='free-corp',
        )
        self.assertEqual(res.status_code, status.HTTP_402_PAYMENT_REQUIRED)
