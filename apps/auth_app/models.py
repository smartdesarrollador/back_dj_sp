"""
Custom User model basado en AbstractBaseUser + PermissionsMixin.

No hereda de BaseModel para evitar conflictos de metaclase con AbstractBaseUser.
Los campos id, created_at y updated_at se definen explícitamente.
AbstractBaseUser provee: password, last_login.
"""
import secrets
import uuid

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models


class UserManager(BaseUserManager):
    """Manager personalizado para el modelo User con email como identificador único."""

    def create_user(
        self,
        email: str,
        name: str,
        password: str | None = None,
        tenant=None,
        **extra_fields,
    ):
        if not email:
            raise ValueError('El email es obligatorio.')
        if tenant is None:
            raise ValueError('El tenant es obligatorio.')

        email = self.normalize_email(email)
        user = self.model(email=email, name=name, tenant=tenant, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(
        self,
        email: str,
        name: str,
        password: str,
        tenant=None,
        **extra_fields,
    ):
        """
        Crea superusuario. Si no se pasa tenant, crea/recupera el tenant 'system'
        para facilitar el `make superuser` inicial sin requerir setup previo.
        """
        if tenant is None:
            from apps.tenants.models import Tenant  # importación tardía para evitar ciclos

            tenant, _ = Tenant.objects.get_or_create(
                slug='system',
                defaults={
                    'name': 'System',
                    'subdomain': 'system',
                    'plan': 'enterprise',
                },
            )

        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('email_verified', True)

        return self.create_user(email, name, password, tenant=tenant, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """
    Usuario del sistema. Pertenece siempre a un Tenant (FK CASCADE).

    Campos de autenticación provistos por AbstractBaseUser: password, last_login.
    Campos de autorización provistos por PermissionsMixin: is_superuser, groups,
    user_permissions.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        'tenants.Tenant',           # string ref para evitar importaciones circulares
        on_delete=models.CASCADE,
        related_name='users',
    )
    email = models.EmailField(unique=True)
    name = models.CharField(max_length=255)
    avatar_url = models.URLField(blank=True)
    email_verified = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    mfa_enabled = models.BooleanField(default=False)
    mfa_secret = models.CharField(max_length=32, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['name']

    class Meta:
        db_table = 'users'
        indexes = [
            models.Index(fields=['email']),
            models.Index(fields=['tenant', 'email']),
        ]

    def __str__(self) -> str:
        return f"{self.email}"


class MFARecoveryCode(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='mfa_recovery_codes')
    code_hash = models.CharField(max_length=128)
    is_used = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'mfa_recovery_codes'

    def __str__(self) -> str:
        return f"RecoveryCode({self.user_id}, used={self.is_used})"


class SSOToken(models.Model):
    """Token opaco single-use TTL=60s para SSO Hub → servicio destino."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='sso_tokens',
    )
    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='sso_tokens',
    )
    service = models.CharField(max_length=50)
    token = models.CharField(max_length=64, unique=True, db_index=True)
    used_at = models.DateTimeField(null=True, blank=True, db_index=True)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        db_table = 'sso_tokens'
        indexes = [
            models.Index(fields=['token'], name='sso_tokens_token_idx'),
            models.Index(fields=['expires_at', 'used_at'], name='sso_tokens_expires_used_idx'),
        ]

    def __str__(self) -> str:
        return f'SSOToken({self.service}, {self.token[:8]}...)'
