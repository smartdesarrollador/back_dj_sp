"""Shared helpers for chat tests."""
from django.contrib.auth import get_user_model

from apps.tenants.models import Tenant

User = get_user_model()

FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


def create_tenant(slug, plan='professional'):
    return Tenant.objects.create(
        name=slug.capitalize(), slug=slug, subdomain=slug, plan=plan
    )


def create_user(tenant, email, name='Test User', superuser=False):
    user = User.objects.create_user(email=email, name=name, password='x', tenant=tenant)
    if superuser:
        user.is_superuser = True
        user.save(update_fields=['is_superuser'])
    return user
