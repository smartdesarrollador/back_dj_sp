"""
Tests for the Chat module — message attachments (Phase 3).
"""
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.chat.models import Conversation, ConversationMember, MessageAttachment
from apps.chat.tests.conftest_helpers import (
    FAST_HASHERS,
    LOCMEM_CACHE,
    create_tenant,
    create_user,
)

BASE = '/api/v1/app/chat/'


@override_settings(PASSWORD_HASHERS=FAST_HASHERS, CACHES=LOCMEM_CACHE)
class TestAttachments(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = create_tenant('att-corp')
        self.alice = create_user(self.tenant, 'alice@att.com', 'Alice Smith')
        self.bob = create_user(self.tenant, 'bob@att.com', 'Bob Jones')
        self.headers = {'HTTP_X_TENANT_SLUG': 'att-corp'}
        self.conv = Conversation.objects.create(tenant=self.tenant, type='direct', created_by=self.alice)
        ConversationMember.objects.create(conversation=self.conv, user=self.alice, role='owner')
        ConversationMember.objects.create(conversation=self.conv, user=self.bob, role='member')
        self.client.force_authenticate(user=self.alice)

    def test_send_message_with_image_attachment(self):
        upload = SimpleUploadedFile('photo.png', b'\x89PNG\r\n\x1a\n fake', content_type='image/png')
        res = self.client.post(
            f'{BASE}messages/',
            {'conversation': str(self.conv.id), 'content': '', 'file': upload},
            format='multipart', **self.headers,
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        attachments = res.json()['attachments']
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0]['kind'], 'image')
        self.assertEqual(attachments[0]['original_name'], 'photo.png')
        self.assertEqual(MessageAttachment.objects.count(), 1)

    def test_send_file_attachment_with_text(self):
        upload = SimpleUploadedFile('notes.txt', b'hello world', content_type='text/plain')
        res = self.client.post(
            f'{BASE}messages/',
            {'conversation': str(self.conv.id), 'content': 'mira esto', 'file': upload},
            format='multipart', **self.headers,
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.json()['content'], 'mira esto')
        self.assertEqual(res.json()['attachments'][0]['kind'], 'file')

    def test_empty_message_without_file_rejected(self):
        res = self.client.post(
            f'{BASE}messages/',
            {'conversation': str(self.conv.id), 'content': '   '},
            format='multipart', **self.headers,
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_oversized_attachment_rejected(self):
        big = SimpleUploadedFile('big.bin', b'x' * (10 * 1024 * 1024 + 1), content_type='application/octet-stream')
        res = self.client.post(
            f'{BASE}messages/',
            {'conversation': str(self.conv.id), 'file': big},
            format='multipart', **self.headers,
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
