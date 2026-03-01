"""
Tests for PASO 8 — Admin User Management endpoints.
Covers: list, create, invite, detail, update, suspend, assign/remove roles.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.rbac.models import Role, UserRole
from apps.tenants.models import Tenant
from core.exceptions import PlanLimitExceeded

User = get_user_model()

USERS_URL = '/api/v1/admin/users/'

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


def _create_tenant(slug, plan='professional'):
    return Tenant.objects.create(
        name=slug.capitalize(), slug=slug, subdomain=slug, plan=plan
    )


def _create_user(tenant, email, superuser=False):
    user = User.objects.create_user(
        email=email, name='Test User', password='pass123', tenant=tenant
    )
    if superuser:
        user.is_superuser = True
        user.save(update_fields=['is_superuser'])
    return user


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestAdminUserViews(APITestCase):
    def setUp(self):
        cache.clear()  # Prevent stale tenant lookups between test savepoints
        self.tenant = _create_tenant('corp')
        self.owner = _create_user(self.tenant, 'owner@corp.com', superuser=True)
        self.owner_role, _ = Role.objects.get_or_create(
            name='Owner',
            tenant=None,
            defaults={'is_system_role': True, 'description': 'Full access'},
        )
        UserRole.objects.create(user=self.owner, role=self.owner_role)
        self.client.force_authenticate(user=self.owner)

    # ── list ──────────────────────────────────────────────────────────────────

    def test_list_requires_auth(self):
        client = APIClient()
        response = client.get(USERS_URL, HTTP_X_TENANT_SLUG='corp')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_list_returns_only_tenant_users(self):
        other_tenant = _create_tenant('other-co')
        _create_user(other_tenant, 'user@other.com')
        response = self.client.get(USERS_URL, HTTP_X_TENANT_SLUG='corp')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        emails = [u['email'] for u in response.json()['users']]
        self.assertIn('owner@corp.com', emails)
        self.assertNotIn('user@other.com', emails)

    # ── create ────────────────────────────────────────────────────────────────

    def test_create_user_success(self):
        payload = {'email': 'new@corp.com', 'name': 'New User', 'password': 'pass1234'}
        response = self.client.post(
            USERS_URL + 'create/', payload, format='json', HTTP_X_TENANT_SLUG='corp'
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(User.objects.filter(email='new@corp.com', tenant=self.tenant).exists())

    def test_create_requires_permission(self):
        regular = _create_user(self.tenant, 'regular@corp.com')
        self.client.force_authenticate(user=regular)
        payload = {'email': 'x@corp.com', 'name': 'X', 'password': 'pass1234'}
        response = self.client.post(
            USERS_URL + 'create/', payload, format='json', HTTP_X_TENANT_SLUG='corp'
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @patch('apps.auth_app.admin_views.check_plan_limit')
    def test_create_exceeds_plan_limit(self, mock_limit):
        mock_limit.side_effect = PlanLimitExceeded()
        payload = {'email': 'over@corp.com', 'name': 'Over', 'password': 'pass1234'}
        response = self.client.post(
            USERS_URL + 'create/', payload, format='json', HTTP_X_TENANT_SLUG='corp'
        )
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)

    # ── suspend ───────────────────────────────────────────────────────────────

    def test_suspend_user_success(self):
        target = _create_user(self.tenant, 'target@corp.com')
        response = self.client.post(
            f'{USERS_URL}{target.id}/suspend/', HTTP_X_TENANT_SLUG='corp'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        target.refresh_from_db()
        self.assertFalse(target.is_active)

    def test_suspend_last_owner_blocked(self):
        response = self.client.post(
            f'{USERS_URL}{self.owner.id}/suspend/', HTTP_X_TENANT_SLUG='corp'
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # ── invite ────────────────────────────────────────────────────────────────

    @patch('apps.auth_app.admin_views.send_mail')
    def test_invite_sends_email(self, mock_mail):
        response = self.client.post(
            USERS_URL + 'invite/',
            {'email': 'invited@corp.com'},
            format='json',
            HTTP_X_TENANT_SLUG='corp',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        mock_mail.assert_called_once()

    # ── assign / remove roles ─────────────────────────────────────────────────

    def test_assign_role_success(self):
        target = _create_user(self.tenant, 'target2@corp.com')
        response = self.client.post(
            f'{USERS_URL}{target.id}/roles/',
            {'role_id': str(self.owner_role.id)},
            format='json',
            HTTP_X_TENANT_SLUG='corp',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(UserRole.objects.filter(user=target, role=self.owner_role).exists())

    def test_assign_cross_tenant_role_blocked(self):
        other_tenant = _create_tenant('other2-co')
        other_role = Role.objects.create(
            name='Custom', tenant=other_tenant, is_system_role=False
        )
        target = _create_user(self.tenant, 'target3@corp.com')
        response = self.client.post(
            f'{USERS_URL}{target.id}/roles/',
            {'role_id': str(other_role.id)},
            format='json',
            HTTP_X_TENANT_SLUG='corp',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_remove_role_success(self):
        target = _create_user(self.tenant, 'target4@corp.com')
        member_role, _ = Role.objects.get_or_create(
            name='Member', tenant=None, defaults={'is_system_role': True}
        )
        user_role = UserRole.objects.create(user=target, role=member_role)
        response = self.client.delete(
            f'{USERS_URL}{target.id}/roles/{member_role.id}/',
            HTTP_X_TENANT_SLUG='corp',
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(UserRole.objects.filter(id=user_role.id).exists())

    def test_remove_last_owner_role_blocked(self):
        response = self.client.delete(
            f'{USERS_URL}{self.owner.id}/roles/{self.owner_role.id}/',
            HTTP_X_TENANT_SLUG='corp',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # ── update ────────────────────────────────────────────────────────────────

    def test_update_user_success(self):
        target = _create_user(self.tenant, 'update@corp.com')
        response = self.client.patch(
            f'{USERS_URL}{target.id}/update/',
            {'name': 'Updated Name'},
            format='json',
            HTTP_X_TENANT_SLUG='corp',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        target.refresh_from_db()
        self.assertEqual(target.name, 'Updated Name')
