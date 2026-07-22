"""
Tests for Plan admin editing — Gestión de Planes, incluye los límites técnicos
editables (Plan.limits) que sobreescriben utils.plans.PLAN_FEATURES.
"""
import uuid

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.subscriptions.models import Plan
from apps.tenants.models import Tenant

User = get_user_model()

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

ADMIN_PLANS_URL = '/api/v1/admin/billing/plans/'
PUBLIC_PLANS_URL = '/api/v1/public/plans/'


def _create_tenant(slug, plan='professional'):
    return Tenant.objects.create(name=slug.capitalize(), slug=slug, subdomain=slug, plan=plan)


def _create_superuser(tenant, email):
    return User.objects.create_user(
        email=email, name='Owner', password='pass123', tenant=tenant, is_superuser=True
    )


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestAdminPlanLimits(APITestCase):
    def setUp(self):
        cache.clear()  # Prevent stale tenant/plan lookups between test savepoints
        # Plan no se siembra vía migración (a propósito, ver plan de implementación) —
        # en prod lo puebla `seed_plans`; en tests hay que crear la fila explícitamente.
        Plan.objects.create(id='free', display_name='Free', description='', popular=False)
        self.tenant = _create_tenant('plan-corp')
        self.owner = _create_superuser(self.tenant, 'owner@plan-corp.com')
        self.client.force_authenticate(user=self.owner)
        self.headers = {'HTTP_X_TENANT_SLUG': 'plan-corp'}

    def test_get_returns_effective_limits_from_code_defaults(self):
        # Plan.limits arranca en {} (default del campo) — get_limits debe devolver
        # el default de PLAN_FEATURES, no un dict vacío.
        response = self.client.get(ADMIN_PLANS_URL, **self.headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        free = next(p for p in response.json()['plans'] if p['id'] == 'free')
        self.assertEqual(free['limits']['max_users'], 5)
        self.assertEqual(free['limits']['storage_gb'], 1)

    def test_patch_limits_persists_and_overrides(self):
        response = self.client.patch(
            f'{ADMIN_PLANS_URL}free/',
            {'limits': {'max_users': 8, 'storage_gb': 2, 'max_projects': 2,
                        'max_custom_roles': 0, 'api_calls_per_month': 1000}},
            format='json', **self.headers,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['plan']['limits']['max_users'], 8)

        plan = Plan.objects.get(id='free')
        self.assertEqual(plan.limits['max_users'], 8)

    def test_patch_upload_limits_persists_and_overrides(self):
        from utils.plans import get_effective_plan_limits

        # Default de código para Free: imagen 2 MB, archivo 5 MB.
        self.assertEqual(get_effective_plan_limits('free')['max_file_upload_mb'], 5)

        response = self.client.patch(
            f'{ADMIN_PLANS_URL}free/',
            {'limits': {'max_image_upload_mb': 4, 'max_file_upload_mb': 7}},
            format='json', **self.headers,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['plan']['limits']['max_file_upload_mb'], 7)

        plan = Plan.objects.get(id='free')
        self.assertEqual(plan.limits['max_image_upload_mb'], 4)
        self.assertEqual(get_effective_plan_limits('free')['max_file_upload_mb'], 7)

    def test_patch_null_limit_means_unlimited(self):
        response = self.client.patch(
            f'{ADMIN_PLANS_URL}free/',
            {'limits': {'max_users': None, 'storage_gb': 1, 'max_projects': 2,
                        'max_custom_roles': 0, 'api_calls_per_month': 1000}},
            format='json', **self.headers,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        from utils.plans import get_plan_limit
        self.assertIsNone(get_plan_limit('free', 'users'))

    def test_patch_invalidates_cache_immediately(self):
        from utils.plans import get_effective_plan_limits

        self.assertEqual(get_effective_plan_limits('free')['max_users'], 5)  # warms cache

        self.client.patch(
            f'{ADMIN_PLANS_URL}free/',
            {'limits': {'max_users': 3, 'storage_gb': 1, 'max_projects': 2,
                        'max_custom_roles': 0, 'api_calls_per_month': 1000}},
            format='json', **self.headers,
        )
        self.assertEqual(get_effective_plan_limits('free')['max_users'], 3)

    def test_check_plan_limit_reflects_override(self):
        from apps.rbac.permissions import check_plan_limit
        from core.exceptions import PlanLimitExceeded

        self.client.patch(
            f'{ADMIN_PLANS_URL}free/',
            {'limits': {'max_users': 2, 'storage_gb': 1, 'max_projects': 2,
                        'max_custom_roles': 0, 'api_calls_per_month': 1000}},
            format='json', **self.headers,
        )

        free_tenant = _create_tenant(f'free-{uuid.uuid4().hex[:8]}', plan='free')
        user = _create_superuser(free_tenant, f'u-{uuid.uuid4().hex[:8]}@free.com')
        user.is_superuser = False
        user.save(update_fields=['is_superuser'])

        with self.assertRaises(PlanLimitExceeded):
            check_plan_limit(user, 'users', current_count=2)

    def test_public_endpoint_reflects_override(self):
        self.client.patch(
            f'{ADMIN_PLANS_URL}free/',
            {'limits': {'max_users': 9, 'storage_gb': 1, 'max_projects': 2,
                        'max_custom_roles': 0, 'api_calls_per_month': 1000}},
            format='json', **self.headers,
        )
        response = self.client.get(PUBLIC_PLANS_URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        free = next(p for p in response.json()['plans'] if p['id'] == 'free')
        self.assertEqual(free['limits']['max_users'], 9)

    def test_upload_limit_override_reaches_features_endpoint(self):
        """Ciclo completo: el Admin baja el tope de archivo y /features/ lo refleja.

        Cierra el bug de fondo de esta fase: que las dos whitelists (escritura en
        PlanLimitsSerializer, lectura en FeaturesView) queden desincronizadas.
        """
        self.client.patch(
            f'{ADMIN_PLANS_URL}free/',
            {'limits': {'max_file_upload_mb': 3}},
            format='json', **self.headers,
        )

        free_tenant = _create_tenant(f'free-{uuid.uuid4().hex[:8]}', plan='free')
        user = _create_superuser(free_tenant, f'u-{uuid.uuid4().hex[:8]}@free.com')
        user.is_superuser = False
        user.save(update_fields=['is_superuser'])

        self.client.force_authenticate(user=user)
        response = self.client.get(
            '/api/v1/features/', HTTP_X_TENANT_SLUG=free_tenant.slug
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['limits']['file_upload_mb'], 3)

    def test_non_superuser_without_permission_forbidden(self):
        regular = _create_superuser(self.tenant, 'regular@plan-corp.com')
        regular.is_superuser = False
        regular.save(update_fields=['is_superuser'])
        self.client.force_authenticate(user=regular)

        response = self.client.patch(
            f'{ADMIN_PLANS_URL}free/', {'display_name': 'x'}, format='json', **self.headers,
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
