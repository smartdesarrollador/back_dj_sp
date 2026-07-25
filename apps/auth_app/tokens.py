"""Custom JWT con tenant_id claim + tokens efímeros vía Redis."""
import secrets

from django.core.cache import cache
from rest_framework_simplejwt.tokens import RefreshToken as BaseRefreshToken

EMAIL_VERIFY_TTL = 86400   # 24h
PASSWORD_RESET_TTL = 3600  # 1h
MFA_SESSION_TTL = 600      # 10 min


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


def create_mfa_session_token(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    cache.set(f'mfa_session:{token}', user_id, timeout=MFA_SESSION_TTL)
    return token


def verify_mfa_session_token(token: str) -> str | None:
    user_id = cache.get(f'mfa_session:{token}')
    if user_id:
        cache.delete(f'mfa_session:{token}')
    return user_id


# 24h — single-use: peek durante las validaciones, consume al éxito. Era 30 min, plazo que
# no sobrevive al pago real ("abro Yape, pago desde otro teléfono, busco el screenshot") y
# dejaba cuentas creadas sin forma de pagar. El token solo permite crear un comprobante
# PENDIENTE de un tenant que ya existe, y un admin lo revisa igual: subirlo a 24h (el mismo
# plazo que la verificación de email) no amplía lo que un tercero podría hacer con él.
PAYMENT_UPLOAD_TTL = 86400


def create_payment_upload_token(tenant_id: str) -> str:
    token = secrets.token_urlsafe(32)
    cache.set(f'payment_upload:{token}', tenant_id, timeout=PAYMENT_UPLOAD_TTL)
    return token


def peek_payment_upload_token(token: str) -> str | None:
    """Lee el tenant_id sin consumir el token — un submit que falla la
    validación (ej. cupón agotado) debe poder reintentarse."""
    return cache.get(f'payment_upload:{token}')


def payment_upload_token_ttl(token: str) -> int | None:
    """
    Segundos que le quedan al token, o None si no existe (nunca existió, caducó o ya se
    consumió). Permite avisar ANTES de que el cliente suba el comprobante, en vez de
    rechazarlo después con un 400 opaco.
    """
    key = f'payment_upload:{token}'
    # `ttl()` es de django-redis; los tests corren con LocMemCache, que no la tiene. Ahí
    # basta con saber si el token existe: el TTL exacto no es lo que se está probando.
    ttl_fn = getattr(cache, 'ttl', None)
    if ttl_fn is None:
        return PAYMENT_UPLOAD_TTL if cache.get(key) is not None else None

    ttl = ttl_fn(key)
    # django-redis: 0 = la clave no existe; None = existe sin expiración (no ocurre aquí,
    # todos se crean con timeout, pero se trata como "sin información de caducidad").
    if not ttl:
        return None
    return int(ttl)


def consume_payment_upload_token(token: str) -> None:
    cache.delete(f'payment_upload:{token}')
