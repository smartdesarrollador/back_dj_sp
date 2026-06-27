"""Shared helpers for vault tests."""
from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model

from apps.tenants.models import Tenant

User = get_user_model()

FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
# A fixed valid Fernet key so encrypt_value/decrypt_value work under @override_settings.
ENC_KEY = Fernet.generate_key().decode()


def create_tenant(slug, plan='professional'):
    return Tenant.objects.create(
        name=slug.capitalize(), slug=slug, subdomain=slug, plan=plan
    )


def create_user(tenant, email, name='Test User'):
    return User.objects.create_user(email=email, name=name, password='x', tenant=tenant)
