"""
Tests for ClientListView — Admin Panel "Clientes" list.
Covers: ClientSubscriptionSerializer reflects Tenant.plan (not the possibly
desynced Subscription.plan) — mismo bug que en el Hub, ver plan de fix
"plan del tenant desincronizado".
"""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.subscriptions.models import Subscription
from apps.tenants.models import Tenant

User = get_user_model()

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

CLIENTS_URL = '/api/v1/admin/clients/'


def _create_tenant(slug, plan='professional'):
    return Tenant.objects.create(name=slug.capitalize(), slug=slug, subdomain=slug, plan=plan)


def _create_superuser(tenant, email):
    return User.objects.create_user(
        email=email, name='Owner', password='pass123', tenant=tenant, is_superuser=True
    )


def _grant_permission(user, codename):
    """Give `user` a fresh role carrying exactly one RBAC permission (non-staff, non-superuser)."""
    permission, _ = Permission.objects.get_or_create(
        codename=codename,
        defaults={'name': codename, 'resource': codename.split('.')[0], 'action': codename.split('.')[1]},
    )
    role = Role.objects.create(tenant=user.tenant, name=f'role-{codename}')
    RolePermission.objects.create(role=role, permission=permission)
    UserRole.objects.create(user=user, role=role)


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestClientListView(APITestCase):
    def setUp(self):
        cache.clear()  # Prevent stale tenant lookups between test savepoints
        self.own_tenant = _create_tenant('own-corp')
        self.owner = _create_superuser(self.own_tenant, 'owner@own-corp.com')
        self.client.force_authenticate(user=self.owner)
        self.headers = {'HTTP_X_TENANT_SLUG': 'own-corp'}

    def test_client_plan_reflects_tenant_plan_when_desynced(self):
        client_tenant = _create_tenant('client-corp', plan='professional')
        sub, _ = Subscription.objects.get_or_create(tenant=client_tenant)
        sub.plan = 'free'
        sub.status = 'active'
        sub.save()

        response = self.client.get(CLIENTS_URL, **self.headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        client = next(c for c in response.json()['clients'] if c['slug'] == 'client-corp')
        self.assertEqual(client['subscription']['plan'], 'professional')


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestClientListViewStaffOnly(APITestCase):
    """
    Regression tests for a broken-access-control bug: 'customers.read'/'customers.suspend'
    are also granted to the shared system 'Owner' role (auto-assigned to every tenant's
    registrant), but these two views return/mutate OTHER tenants' data. RBAC permission
    alone must never be sufficient here — IsStaffUser is required in addition.
    """

    def setUp(self):
        cache.clear()
        self.own_tenant = _create_tenant('own-corp')
        self.other_tenant = _create_tenant('other-corp')

    def test_non_staff_user_with_customers_permission_is_blocked(self):
        owner = User.objects.create_user(
            email='owner@own-corp.com', name='Owner', password='pass123', tenant=self.own_tenant,
        )
        _grant_permission(owner, 'customers.read')
        self.client.force_authenticate(user=owner)

        response = self.client.get(CLIENTS_URL, **{'HTTP_X_TENANT_SLUG': 'own-corp'})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_non_staff_user_with_customers_suspend_permission_cannot_suspend_other_tenant(self):
        owner = User.objects.create_user(
            email='owner2@own-corp.com', name='Owner', password='pass123', tenant=self.own_tenant,
        )
        _grant_permission(owner, 'customers.suspend')
        self.client.force_authenticate(user=owner)

        response = self.client.post(
            f'/api/v1/admin/clients/{self.other_tenant.pk}/suspend/',
            {'active': False},
            **{'HTTP_X_TENANT_SLUG': 'own-corp'},
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.other_tenant.refresh_from_db()
        self.assertTrue(self.other_tenant.is_active)

    def test_staff_without_rbac_permission_is_still_blocked(self):
        staff = User.objects.create_user(
            email='staff@own-corp.com', name='Staff', password='pass123',
            tenant=self.own_tenant, is_staff=True,
        )
        self.client.force_authenticate(user=staff)

        response = self.client.get(CLIENTS_URL, **{'HTTP_X_TENANT_SLUG': 'own-corp'})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_with_rbac_permission_succeeds(self):
        staff = User.objects.create_user(
            email='staff2@own-corp.com', name='Staff', password='pass123',
            tenant=self.own_tenant, is_staff=True,
        )
        _grant_permission(staff, 'customers.read')
        self.client.force_authenticate(user=staff)

        response = self.client.get(CLIENTS_URL, **{'HTTP_X_TENANT_SLUG': 'own-corp'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
