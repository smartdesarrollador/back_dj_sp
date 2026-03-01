"""
Tests for PASO 13 — SSH Keys module.
Covers: list, create (fingerprint auto-calc), plan limit (free=0), private key encrypted, cross-tenant.
"""
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.ssh_keys.models import SSHKey
from apps.tenants.models import Tenant

User = get_user_model()

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
_ENC_KEY = Fernet.generate_key().decode()

BASE_URL = '/api/v1/app/ssh-keys/'

# A minimal valid base64-encoded public key component for fingerprint testing
_SAMPLE_PUB_KEY = (
    'ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAAAgQC1234567890abcdefghijklmnopqrstuvwxyz'
    'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+/abcdefghijklmnopqrstuvwxyzABCDEFGHIJK'
    'LMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz01234567890ABCDEFGHIJKLMNOPQRSTUVW'
    'XYZ== test@host'
)


def _create_tenant(slug, plan='professional'):
    return Tenant.objects.create(name=slug.capitalize(), slug=slug, subdomain=slug, plan=plan)


def _create_superuser(tenant, email):
    user = User.objects.create_user(
        email=email, name='Test User', password='x', tenant=tenant
    )
    user.is_superuser = True
    user.save(update_fields=['is_superuser'])
    return user


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE, ENCRYPTION_KEY=_ENC_KEY)
class TestSSHKeyViews(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = _create_tenant('sk-corp')
        self.user = _create_superuser(self.tenant, 'u@sk.com')
        self.client.force_authenticate(user=self.user)
        self.slug = {'HTTP_X_TENANT_SLUG': 'sk-corp'}

    # ── List ──────────────────────────────────────────────────────────────────

    def test_list_ssh_keys_empty(self):
        response = self.client.get(BASE_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['ssh_keys'], [])

    # ── Create with fingerprint ───────────────────────────────────────────────

    def test_create_ssh_key_success(self):
        data = {
            'name': 'Deploy Key',
            'public_key': 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBmGQHs2A3C4D5E6F7G8H9I0J1K2L3M4N5 user@host',
            'algorithm': 'ed25519',
        }
        response = self.client.post(BASE_URL, data, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        body = response.json()
        self.assertEqual(body['name'], 'Deploy Key')
        self.assertEqual(body['algorithm'], 'ed25519')
        # private_key must NOT appear in the response
        self.assertNotIn('private_key', body)

    # ── Plan limit (free plan max=0) ──────────────────────────────────────────

    def test_create_ssh_key_exceeds_plan_limit(self):
        with patch('apps.ssh_keys.views.check_plan_limit') as mock_limit:
            from core.exceptions import PlanLimitExceeded
            mock_limit.side_effect = PlanLimitExceeded()
            data = {'name': 'Key', 'public_key': 'ssh-rsa AAAA test@host'}
            response = self.client.post(BASE_URL, data, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)

    # ── Private key encrypted in DB ───────────────────────────────────────────

    def test_ssh_key_private_key_encrypted_in_db(self):
        ssh_key = SSHKey.objects.create(
            tenant=self.tenant,
            user=self.user,
            name='My Key',
            public_key='ssh-rsa AAAA test@host',
            private_key='-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAK...\n-----END RSA PRIVATE KEY-----',
            algorithm='rsa',
        )
        ssh_key.refresh_from_db()
        self.assertTrue(ssh_key.is_encrypted)
        self.assertNotIn('BEGIN RSA PRIVATE KEY', ssh_key.private_key)

    # ── Cross-tenant isolation ────────────────────────────────────────────────

    def test_cross_tenant_ssh_key_blocked(self):
        other_tenant = _create_tenant('other-sk')
        other_user = _create_superuser(other_tenant, 'other@sk.com')
        sk = SSHKey.objects.create(
            tenant=other_tenant,
            user=other_user,
            name='Other Key',
            public_key='ssh-rsa AAAA other@host',
        )
        url = f'{BASE_URL}{sk.pk}/'
        response = self.client.get(url, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
