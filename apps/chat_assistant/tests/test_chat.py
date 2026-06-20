"""
Tests for Chat Assistant app.

Group 1: Session management (3 tests)
Group 2: Knowledge base CRUD (5 tests)
Group 3: Message validation + rate limit (3 tests)
"""
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.chat_assistant.models import ChatKnowledgeArticle, ChatMessage, ChatSession

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

SESSION_URL = '/api/v1/public/chat/session/'
MESSAGE_URL = '/api/v1/public/chat/message/'
HISTORY_URL = '/api/v1/public/chat/history/'
KB_URL = '/api/v1/admin/knowledge-base/'


def _make_article(**kwargs) -> ChatKnowledgeArticle:
    defaults = {
        'title': 'Planes disponibles',
        'content': 'Ofrecemos Free, Starter, Professional y Enterprise.',
        'category': 'pricing',
        'keywords': ['planes', 'precios'],
        'is_active': True,
        'order': 0,
    }
    defaults.update(kwargs)
    return ChatKnowledgeArticle.objects.create(**defaults)


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestChatSession(APITestCase):
    """Group 1: Session management."""

    def setUp(self):
        cache.clear()

    def test_create_session_without_token(self):
        """POST without session_token → generates one automatically."""
        response = self.client.post(SESSION_URL, {}, content_type='application/json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('session_token', data)
        self.assertGreater(len(data['session_token']), 10)
        self.assertTrue(ChatSession.objects.filter(session_token=data['session_token']).exists())

    def test_create_session_with_existing_token(self):
        """POST with existing token → returns same session (idempotent)."""
        session = ChatSession.objects.create(session_token='test-token-abc123')
        response = self.client.post(
            SESSION_URL,
            {'session_token': 'test-token-abc123'},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['session_token'], 'test-token-abc123')
        self.assertEqual(ChatSession.objects.filter(session_token='test-token-abc123').count(), 1)

    def test_history_returns_messages(self):
        """GET /history/?session_token=... → returns message list."""
        session = ChatSession.objects.create(session_token='hist-token')
        ChatMessage.objects.create(session=session, role='user', content='Hola')
        ChatMessage.objects.create(session=session, role='assistant', content='¡Hola!')

        response = self.client.get(f'{HISTORY_URL}?session_token=hist-token')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        messages = response.json()['messages']
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]['role'], 'user')
        self.assertEqual(messages[1]['role'], 'assistant')


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestKnowledgeBaseCRUD(APITestCase):
    """Group 2: Knowledge base admin endpoints."""

    def setUp(self):
        cache.clear()
        from django.contrib.auth import get_user_model
        User = get_user_model()
        from apps.tenants.models import Tenant
        self.tenant = Tenant.objects.create(name='TestCo', slug='testco', subdomain='testco')
        self.superuser = User.objects.create_user(
            email='admin@test.com', name='Admin', password='x', tenant=self.tenant
        )
        self.superuser.is_superuser = True
        self.superuser.save(update_fields=['is_superuser'])
        self.client.force_authenticate(user=self.superuser)
        self.slug_header = {'HTTP_X_TENANT_SLUG': 'testco'}

    def test_list_articles_empty(self):
        response = self.client.get(KB_URL, **self.slug_header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['articles'], [])

    def test_create_article(self):
        data = {
            'title': 'Plan Starter',
            'content': 'El plan Starter cuesta $19/mes.',
            'category': 'pricing',
            'keywords': ['starter', 'precio'],
            'order': 1,
        }
        response = self.client.post(KB_URL, data, format='json', **self.slug_header)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.json()['title'], 'Plan Starter')
        self.assertTrue(ChatKnowledgeArticle.objects.filter(title='Plan Starter').exists())

    def test_update_article(self):
        article = _make_article()
        url = f'{KB_URL}{article.pk}/'
        response = self.client.patch(
            url,
            {'title': 'Planes actualizados'},
            format='json',
            **self.slug_header,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['title'], 'Planes actualizados')

    def test_toggle_article(self):
        article = _make_article(is_active=True)
        url = f'{KB_URL}{article.pk}/toggle/'
        response = self.client.post(url, **self.slug_header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.json()['is_active'])
        article.refresh_from_db()
        self.assertFalse(article.is_active)

    def test_delete_article(self):
        article = _make_article()
        url = f'{KB_URL}{article.pk}/'
        response = self.client.delete(url, **self.slug_header)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(ChatKnowledgeArticle.objects.filter(pk=article.pk).exists())


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestChatMessage(APITestCase):
    """Group 3: Message validation + session limit."""

    def setUp(self):
        cache.clear()
        self.session = ChatSession.objects.create(session_token='msg-token-xyz')

    def test_message_missing_fields_returns_400(self):
        response = self.client.post(MESSAGE_URL, {}, content_type='application/json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_message_invalid_token_returns_404(self):
        response = self.client.post(
            MESSAGE_URL,
            {'session_token': 'nonexistent', 'message': 'Hola'},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @patch('apps.chat_assistant.views.stream_chat_response')
    def test_session_limit_returns_429(self, mock_stream):
        """Sessions at MAX_MESSAGES_PER_SESSION return 429."""
        from apps.chat_assistant.services import MAX_MESSAGES_PER_SESSION
        self.session.message_count = MAX_MESSAGES_PER_SESSION
        self.session.save(update_fields=['message_count'])

        response = self.client.post(
            MESSAGE_URL,
            {'session_token': 'msg-token-xyz', 'message': 'Hola'},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        mock_stream.assert_not_called()
