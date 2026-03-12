"""Tests que verifican integridad de fixtures de permisos y roles del sistema."""
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APITestCase

from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.tenants.models import Tenant

UserModel = get_user_model()

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS)
class TestPermissionsFixture(TestCase):
    fixtures = ['permissions']

    def test_total_permission_count(self) -> None:
        self.assertEqual(Permission.objects.count(), 64)

    def test_referrals_read_exists(self) -> None:
        # Should not raise DoesNotExist
        Permission.objects.get(codename='referrals.read')

    def test_referrals_manage_exists(self) -> None:
        Permission.objects.get(codename='referrals.manage')

    def test_no_duplicate_codenames(self) -> None:
        total = Permission.objects.count()
        distinct = Permission.objects.values('codename').distinct().count()
        self.assertEqual(distinct, total)


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS)
class TestSystemRolesFixture(TestCase):
    fixtures = ['permissions', 'system_roles']

    def test_four_system_roles_exist(self) -> None:
        self.assertEqual(Role.objects.filter(is_system_role=True).count(), 4)

    def test_owner_has_referrals_read(self) -> None:
        owner = Role.objects.get(pk='e813d436-0689-41e8-a207-4648631e6dba')
        perm = Permission.objects.get(codename='referrals.read')
        self.assertTrue(
            RolePermission.objects.filter(role=owner, permission=perm).exists()
        )

    def test_owner_has_referrals_manage(self) -> None:
        owner = Role.objects.get(pk='e813d436-0689-41e8-a207-4648631e6dba')
        perm = Permission.objects.get(codename='referrals.manage')
        self.assertTrue(
            RolePermission.objects.filter(role=owner, permission=perm).exists()
        )

    def test_member_has_referrals_read_not_manage(self) -> None:
        member = Role.objects.get(pk='e296c4b9-2097-40e7-afdb-242ad5d76fe9')
        read_perm = Permission.objects.get(codename='referrals.read')
        manage_perm = Permission.objects.get(codename='referrals.manage')
        self.assertTrue(
            RolePermission.objects.filter(role=member, permission=read_perm).exists()
        )
        self.assertFalse(
            RolePermission.objects.filter(role=member, permission=manage_perm).exists()
        )


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS)
class TestReferralPermissionIntegration(APITestCase):
    """Verifica que HasPermission('referrals.read') funciona con roles reales (sin fixtures)."""

    REFERRAL_URL = '/api/v1/app/referrals/'

    def _make_tenant(self) -> Tenant:
        return Tenant.objects.create(
            slug=f't-{uuid.uuid4().hex[:6]}',
            name='Test Corp',
            subdomain=f's-{uuid.uuid4().hex[:6]}',
        )

    def _make_user(self, tenant: Tenant, is_superuser: bool = False):  # noqa: ANN201
        email = f'u-{uuid.uuid4().hex[:6]}@t.com'
        if is_superuser:
            return UserModel.objects.create_superuser(
                email=email, name='Test User', password='pw', tenant=tenant,
            )
        return UserModel.objects.create_user(
            email=email, name='Test User', password='pw', tenant=tenant,
        )

    def _grant_referrals_read(self, user, tenant: Tenant) -> None:  # noqa: ANN001
        perm, _ = Permission.objects.get_or_create(
            codename='referrals.read',
            defaults={'name': 'Ver Referidos', 'resource': 'referrals', 'action': 'read'},
        )
        role = Role.objects.create(tenant=tenant, name='hub-user')
        RolePermission.objects.create(role=role, permission=perm, scope='all')
        UserRole.objects.create(user=user, role=role)

    def test_user_with_referrals_read_can_access(self) -> None:
        tenant = self._make_tenant()
        user = self._make_user(tenant)
        self._grant_referrals_read(user, tenant)
        self.client.force_authenticate(user=user)
        resp = self.client.get(self.REFERRAL_URL, HTTP_X_TENANT_SLUG=tenant.slug)
        self.assertEqual(resp.status_code, 200)

    def test_user_without_permission_gets_403(self) -> None:
        tenant = self._make_tenant()
        user = self._make_user(tenant)
        self.client.force_authenticate(user=user)
        resp = self.client.get(self.REFERRAL_URL, HTTP_X_TENANT_SLUG=tenant.slug)
        self.assertEqual(resp.status_code, 403)

    def test_superuser_bypasses_permission(self) -> None:
        tenant = self._make_tenant()
        user = self._make_user(tenant, is_superuser=True)
        self.client.force_authenticate(user=user)
        resp = self.client.get(self.REFERRAL_URL, HTTP_X_TENANT_SLUG=tenant.slug)
        self.assertEqual(resp.status_code, 200)

    def test_unauthenticated_gets_401(self) -> None:
        resp = self.client.get(self.REFERRAL_URL)
        self.assertEqual(resp.status_code, 401)
