"""
Tests for the Chat WebSocket JWT auth middleware (Phase 3).
Validates token → user resolution without spinning up Redis/Channels.
"""
from django.test import TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from apps.chat.middleware import resolve_user
from apps.chat.tests.conftest_helpers import FAST_HASHERS, create_tenant, create_user


@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class TestWSAuthMiddleware(TestCase):
    def setUp(self):
        self.tenant = create_tenant('ws-corp')
        self.user = create_user(self.tenant, 'ws@corp.com', 'WS User')

    def test_valid_token_resolves_user(self):
        token = str(AccessToken.for_user(self.user))
        resolved = resolve_user(token)
        self.assertEqual(resolved.id, self.user.id)
        self.assertTrue(resolved.is_authenticated)

    def test_invalid_token_is_anonymous(self):
        self.assertTrue(resolve_user('not-a-real-token').is_anonymous)

    def test_empty_token_is_anonymous(self):
        self.assertTrue(resolve_user('').is_anonymous)
