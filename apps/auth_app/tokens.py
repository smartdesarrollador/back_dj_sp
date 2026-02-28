"""Custom JWT con tenant_id claim + tokens efímeros vía Redis."""
import secrets

from django.core.cache import cache
from rest_framework_simplejwt.tokens import RefreshToken as BaseRefreshToken

EMAIL_VERIFY_TTL = 86400   # 24h
PASSWORD_RESET_TTL = 3600  # 1h


class TenantRefreshToken(BaseRefreshToken):
    @classmethod
    def for_user(cls, user):
        token = super().for_user(user)
        token['tenant_id'] = str(user.tenant_id)
        token['name'] = user.name
        token['email'] = user.email
        return token


def create_email_verification_token(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    cache.set(f'email_verify:{token}', user_id, timeout=EMAIL_VERIFY_TTL)
    return token


def verify_email_token(token: str) -> str | None:
    user_id = cache.get(f'email_verify:{token}')
    if user_id:
        cache.delete(f'email_verify:{token}')
    return user_id


def create_password_reset_token(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    cache.set(f'password_reset:{token}', user_id, timeout=PASSWORD_RESET_TTL)
    return token


def verify_password_reset_token(token: str) -> str | None:
    user_id = cache.get(f'password_reset:{token}')
    if user_id:
        cache.delete(f'password_reset:{token}')
    return user_id
