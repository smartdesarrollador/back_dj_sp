"""
Tests for PASO 8 — Admin Role and Permission Management endpoints.
Covers: list roles, create, detail, update, delete, permissions update, permission list.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.tenants.models import Tenant
from core.exceptions import PlanLimitExceeded

User = get_user_model()

ROLES_URL = '/api/v1/admin/roles/'
PERMISSIONS_URL = '/api/v1/admin/permissions/'

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


def _make_permission(codename='test.read'):
    resource, _, action = codename.partition('.')
    perm, _ = Permission.objects.get_or_create(
        codename=codename,
        defaults={'name': codename, 'resource': resource, 'action': action},
    )
    return perm


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestAdminRoleViews(APITestCase):
    def setUp(self):
        cache.clear()  # Prevent stale tenant lookups between test savepoints
        self.tenant = _create_tenant('roles-corp')
        self.admin = _create_user(self.tenant, 'admin@roles.com', superuser=True)
        self.system_role, _ = Role.objects.get_or_create(
            name='Owner',
            tenant=None,
            defaults={'is_system_role': True, 'description': 'Full access'},
        )
        UserRole.objects.create(user=self.admin, role=self.system_role)
        self.client.force_authenticate(user=self.admin)

    # ── list ──────────────────────────────────────────────────────────────────

    def test_list_roles_includes_system_and_custom(self):
        custom = Role.objects.create(
            name='Custom', tenant=self.tenant, is_system_role=False
        )
        response = self.client.get(ROLES_URL, HTTP_X_TENANT_SLUG='roles-corp')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        names = [r['name'] for r in response.json()['roles']]
        self.assertIn('Owner', names)
        self.assertIn('Custom', names)

    def test_list_requires_auth(self):
        client = APIClient()
        response = client.get(ROLES_URL, HTTP_X_TENANT_SLUG='roles-corp')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    # ── create ────────────────────────────────────────────────────────────────

    def test_create_role_success(self):
        perm = _make_permission('tasks.read')
        payload = {
            'name': 'Editor',
            'description': 'Can edit tasks',
            'permission_ids': [str(perm.id)],
        }
        response = self.client.post(
            ROLES_URL + 'create/', payload, format='json', HTTP_X_TENANT_SLUG='roles-corp'
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            Role.objects.filter(name='Editor', tenant=self.tenant, is_system_role=False).exists()
        )

    def test_create_requires_feature(self):
        # Free plan: custom_roles feature is False
        free_tenant = _create_tenant('free-co', plan='free')
        free_user = _create_user(free_tenant, 'free@free.com', superuser=True)
        self.client.force_authenticate(user=free_user)
        payload = {'name': 'SomeRole', 'description': ''}
        response = self.client.post(
            ROLES_URL + 'create/', payload, format='json', HTTP_X_TENANT_SLUG='free-co'
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @patch('apps.rbac.views.check_plan_limit')
    def test_create_exceeds_plan_limit(self, mock_limit):
        mock_limit.side_effect = PlanLimitExceeded()
        payload = {'name': 'OverLimit', 'description': ''}
        response = self.client.post(
            ROLES_URL + 'create/', payload, format='json', HTTP_X_TENANT_SLUG='roles-corp'
        )
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)

    # ── update / delete system roles ──────────────────────────────────────────

    def test_update_system_role_blocked(self):
        response = self.client.patch(
            f'{ROLES_URL}{self.system_role.id}/update/',
            {'description': 'new desc'},
            format='json',
            HTTP_X_TENANT_SLUG='roles-corp',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_delete_system_role_blocked(self):
        response = self.client.delete(
            f'{ROLES_URL}{self.system_role.id}/delete/',
            HTTP_X_TENANT_SLUG='roles-corp',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # ── delete custom role ────────────────────────────────────────────────────

    def test_delete_custom_role_success(self):
        custom = Role.objects.create(
            name='Deletable', tenant=self.tenant, is_system_role=False
        )
        response = self.client.delete(
            f'{ROLES_URL}{custom.id}/delete/',
            HTTP_X_TENANT_SLUG='roles-corp',
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Role.objects.filter(id=custom.id).exists())

    # ── permissions replace ───────────────────────────────────────────────────

    def test_update_permissions_replaces_all(self):
        custom = Role.objects.create(
            name='Replaceable', tenant=self.tenant, is_system_role=False
        )
        old_perm = _make_permission('tasks.create')
        new_perm = _make_permission('tasks.delete')
        RolePermission.objects.create(role=custom, permission=old_perm)

        response = self.client.put(
            f'{ROLES_URL}{custom.id}/permissions/',
            {'permission_ids': [str(new_perm.id)]},
            format='json',
            HTTP_X_TENANT_SLUG='roles-corp',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        remaining = list(
            custom.role_permissions.values_list('permission__codename', flat=True)
        )
        self.assertNotIn('tasks.create', remaining)
        self.assertIn('tasks.delete', remaining)

    # ── permission list ───────────────────────────────────────────────────────

    def test_permission_list(self):
        _make_permission('roles.read')
        _make_permission('roles.create')
        response = self.client.get(PERMISSIONS_URL, HTTP_X_TENANT_SLUG='roles-corp')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('permissions', data)
        self.assertIsInstance(data['permissions'], list)
