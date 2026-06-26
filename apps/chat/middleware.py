"""
Channels middleware — authenticate WebSocket connections via a JWT access token
passed in the query string (``?token=<access>``).
"""
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth.models import AnonymousUser


def resolve_user(token: str):
    """Sync token → user resolution (testable without the async wrapper)."""
    from rest_framework_simplejwt.tokens import AccessToken
    from django.contrib.auth import get_user_model

    User = get_user_model()
    if not token:
        return AnonymousUser()
    try:
        access = AccessToken(token)
        return User.objects.get(id=access['user_id'], is_active=True)
    except Exception:
        return AnonymousUser()


_get_user = database_sync_to_async(resolve_user)


class JWTAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        query = parse_qs((scope.get('query_string') or b'').decode())
        token = (query.get('token') or [None])[0]
        scope['user'] = await _get_user(token) if token else AnonymousUser()
        return await super().__call__(scope, receive, send)
