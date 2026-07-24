"""
Tests for the Chat module — message attachments (Phase 3).
"""
from unittest.mock import patch

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.chat.models import Conversation, ConversationMember, Message, MessageAttachment
from apps.chat.tests.conftest_helpers import (
    FAST_HASHERS,
    LOCMEM_CACHE,
    create_tenant,
    create_user,
)
from core.exceptions import PlanLimitExceeded
from core.tests.helpers import png_bytes

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
        upload = SimpleUploadedFile('photo.png', png_bytes(), content_type='image/png')
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

    def test_disallowed_extension_rejected(self):
        big = SimpleUploadedFile(
            'app.bin', b'x' * 1024, content_type='application/octet-stream'
        )
        res = self.client.post(
            f'{BASE}messages/',
            {'conversation': str(self.conv.id), 'file': big},
            format='multipart', **self.headers,
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(MessageAttachment.objects.count(), 0)

    def test_oversized_attachment_rejected(self):
        # Se baja el tope del plan desde el Admin en vez de materializar los 25 MB del
        # default de 'professional': de paso comprueba que el override llega al endpoint.
        from apps.subscriptions.models import Plan
        Plan.objects.create(
            id='professional', display_name='Professional', limits={'max_file_upload_mb': 1},
        )
        big = SimpleUploadedFile(
            'doc.pdf', b'%PDF-1.4' + b'x' * 1024 * 1024, content_type='application/pdf',
        )
        res = self.client.post(
            f'{BASE}messages/',
            {'conversation': str(self.conv.id), 'file': big},
            format='multipart', **self.headers,
        )
        self.assertEqual(res.status_code, status.HTTP_402_PAYMENT_REQUIRED)
        self.assertEqual(MessageAttachment.objects.count(), 0)

    def test_executable_renamed_to_png_rejected(self):
        fake = SimpleUploadedFile(
            'inocente.png', b'MZ\x90\x00\x03\x00\x00\x00', content_type='image/png'
        )
        res = self.client.post(
            f'{BASE}messages/',
            {'conversation': str(self.conv.id), 'file': fake},
            format='multipart', **self.headers,
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(MessageAttachment.objects.count(), 0)

    # El módulo de origen, no apps.chat.views: validate_upload importa
    # check_storage_limit dentro de la función.
    @patch('apps.rbac.permissions.check_storage_limit')
    def test_attachment_over_storage_limit_rejected(self, mock_limit):
        mock_limit.side_effect = PlanLimitExceeded()
        upload = SimpleUploadedFile('photo.png', png_bytes(), content_type='image/png')
        res = self.client.post(
            f'{BASE}messages/',
            {'conversation': str(self.conv.id), 'file': upload},
            format='multipart', **self.headers,
        )
        self.assertEqual(res.status_code, status.HTTP_402_PAYMENT_REQUIRED)
        self.assertEqual(MessageAttachment.objects.count(), 0)
        self.assertEqual(Message.objects.count(), 0)

    def test_delete_own_message_frees_storage(self):
        import tempfile

        from django.core.files.storage import default_storage

        from utils.storage import get_tenant_storage_bytes

        with override_settings(MEDIA_ROOT=tempfile.mkdtemp()):
            msg = Message.objects.create(conversation=self.conv, sender=self.alice, content='')
            att = MessageAttachment.objects.create(
                message=msg,
                file=SimpleUploadedFile('f.png', png_bytes(), content_type='image/png'),
                kind='image', original_name='f.png', size=4096,
            )
            name = att.file.name
            self.assertEqual(get_tenant_storage_bytes(self.tenant), 4096)

            res = self.client.delete(f'{BASE}messages/{msg.id}/', **self.headers)

            self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
            # Soft-delete: la fila permanece como tombstone, pero el adjunto se borra de verdad.
            msg.refresh_from_db()
            self.assertIsNotNone(msg.deleted_at)
            self.assertFalse(MessageAttachment.objects.filter(id=att.id).exists())
            self.assertEqual(get_tenant_storage_bytes(self.tenant), 0)  # cuota liberada
            self.assertFalse(default_storage.exists(name))  # binario borrado del disco

    def test_cannot_delete_another_users_message(self):
        # alice (autenticada) intenta borrar un mensaje de bob → 404, sigue existiendo.
        msg = Message.objects.create(conversation=self.conv, sender=self.bob, content='hola')
        res = self.client.delete(f'{BASE}messages/{msg.id}/', **self.headers)
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(Message.objects.filter(id=msg.id).exists())
