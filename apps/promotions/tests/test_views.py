"""
Tests del CRUD admin de promociones (/api/v1/admin/promotions/).

Covers: contrato {promotions: [...]} del listado, validaciones del write
serializer (código inmutable/uppercase, rangos por tipo, fechas, max_uses),
composición IsStaffUser + HasPermission('promotions.manage') (ambos
load-bearing), DELETE con guard 409 si existe cualquier canje, stats y
métricas anotadas del listado.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.promotions.models import Promotion, PromotionRedemption
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.tenants.models import Tenant

User = get_user_model()

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

LIST_URL = '/api/v1/admin/promotions/'


def _error_details(response) -> dict:
    """El exception handler del repo envuelve errores: {'error': {'details': {...}}}."""
    return response.data.get('error', {}).get('details', {})


def _detail_url(promotion_id) -> str:
    return f'{LIST_URL}{promotion_id}/'


def _stats_url(promotion_id) -> str:
    return f'{LIST_URL}{promotion_id}/stats/'


def _create_tenant(slug, plan='professional'):
    return Tenant.objects.create(name=slug.capitalize(), slug=slug, subdomain=slug, plan=plan)


def _create_staff(tenant, email):
    return User.objects.create_user(
        email=email, name='Staff', password='pass123', tenant=tenant, is_staff=True,
    )


def _grant_permission(user, codename):
    permission, _ = Permission.objects.get_or_create(
        codename=codename,
        defaults={'name': codename, 'resource': codename.split('.')[0], 'action': codename.split('.')[1]},
    )
    role = Role.objects.create(tenant=user.tenant, name=f'role-{codename}')
    RolePermission.objects.create(role=role, permission=permission)
    UserRole.objects.create(user=user, role=role)


def _create_promotion(**overrides) -> Promotion:
    now = timezone.now()
    defaults = {
        'code': 'VERANO20',
        'name': 'Promo Verano',
        'type': 'percentage',
        'value': Decimal('20'),
        'applicable_plans': ['starter', 'professional'],
        'starts_at': now - timedelta(days=1),
        'expires_at': now + timedelta(days=30),
    }
    defaults.update(overrides)
    return Promotion.objects.create(**defaults)


def _create_redemption(promotion, tenant, status='confirmed', plan='starter',
                       original='19.00', discount='3.80', final='15.20'):
    return PromotionRedemption.objects.create(
        promotion=promotion, tenant=tenant, plan=plan,
        original_amount=Decimal(original), discount_amount=Decimal(discount),
        final_amount=Decimal(final), status=status,
        confirmed_at=timezone.now() if status == 'confirmed' else None,
    )


def _payload(**overrides) -> dict:
    now = timezone.now()
    payload = {
        'code': 'NUEVO25',
        'name': 'Promo Nueva',
        'description': '',
        'type': 'percentage',
        'value': 25,
        'max_discount': None,
        'applicable_plans': ['starter'],
        'new_customers_only': True,
        'starts_at': (now - timedelta(days=1)).isoformat(),
        'expires_at': (now + timedelta(days=30)).isoformat(),
        'max_uses': 100,
        'max_uses_per_customer': 1,
    }
    payload.update(overrides)
    return payload


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class PromotionCrudTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = _create_tenant('plan-corp')
        self.staff = _create_staff(self.tenant, 'staff@plan-corp.com')
        _grant_permission(self.staff, 'promotions.manage')
        self.client.force_authenticate(user=self.staff)
        self.headers = {'HTTP_X_TENANT_SLUG': 'plan-corp'}

    # ---- Listado ----

    def test_list_empty_returns_wrapped_contract(self):
        response = self.client.get(LIST_URL, **self.headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {'promotions': []})

    def test_list_includes_computed_status_and_metrics(self):
        promo = _create_promotion()
        other_tenant = _create_tenant('other-corp')
        _create_redemption(promo, self.tenant, status='confirmed')
        _create_redemption(promo, other_tenant, status='confirmed',
                           original='79.00', discount='15.80', final='63.20', plan='professional')
        _create_redemption(promo, other_tenant, status='released')
        _create_redemption(promo, other_tenant, status='pending')

        response = self.client.get(LIST_URL, **self.headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data['promotions'][0]
        self.assertEqual(data['code'], 'VERANO20')
        self.assertEqual(data['status'], 'active')
        self.assertEqual(data['value'], Decimal('20'))
        # 2 confirmadas / (2 confirmadas + 1 liberada) — pending no cuenta
        self.assertAlmostEqual(data['conversion_rate'], 66.7)
        self.assertAlmostEqual(data['total_revenue'], 78.40)
        self.assertAlmostEqual(data['avg_discount_amount'], 9.80)

    # ---- Create ----

    def test_create_normalizes_code_to_uppercase(self):
        response = self.client.post(
            LIST_URL, _payload(code='nuevo25'), format='json', **self.headers,
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['code'], 'NUEVO25')
        self.assertEqual(response.data['status'], 'active')
        self.assertTrue(Promotion.objects.filter(code='NUEVO25').exists())

    def test_create_duplicate_code_fails(self):
        _create_promotion(code='NUEVO25')
        response = self.client.post(LIST_URL, _payload(), format='json', **self.headers)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('code', _error_details(response))

    def test_create_rejects_bad_code_format(self):
        for bad_code in ['ab', 'X' * 21, 'CON ESPACIO', 'GUION-NO']:
            response = self.client.post(
                LIST_URL, _payload(code=bad_code), format='json', **self.headers,
            )
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, bad_code)

    def test_create_rejects_inverted_dates(self):
        now = timezone.now()
        response = self.client.post(
            LIST_URL,
            _payload(starts_at=(now + timedelta(days=10)).isoformat(),
                     expires_at=(now + timedelta(days=1)).isoformat()),
            format='json', **self.headers,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('expires_at', _error_details(response))

    def test_create_rejects_percentage_out_of_range(self):
        for value in [0, 101, -5]:
            response = self.client.post(
                LIST_URL, _payload(value=value), format='json', **self.headers,
            )
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, value)

    def test_create_rejects_non_positive_fixed_amount(self):
        response = self.client.post(
            LIST_URL, _payload(type='fixed_amount', value=0), format='json', **self.headers,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_rejects_trial_extension_type(self):
        response = self.client.post(
            LIST_URL, _payload(type='trial_extension', value=30), format='json', **self.headers,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('type', _error_details(response))

    def test_create_rejects_invalid_or_empty_plans(self):
        for plans in [[], ['free'], ['starter', 'premium']]:
            response = self.client.post(
                LIST_URL, _payload(applicable_plans=plans), format='json', **self.headers,
            )
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, plans)

    def test_create_rejects_max_discount_on_fixed_amount(self):
        response = self.client.post(
            LIST_URL, _payload(type='fixed_amount', value=10, max_discount=5),
            format='json', **self.headers,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('max_discount', _error_details(response))

    # ---- Update ----

    def test_patch_pause_and_resume_via_status(self):
        promo = _create_promotion()
        response = self.client.patch(
            _detail_url(promo.id), {'status': 'paused'}, format='json', **self.headers,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'paused')
        promo.refresh_from_db()
        self.assertTrue(promo.is_paused)

        response = self.client.patch(
            _detail_url(promo.id), {'status': 'active'}, format='json', **self.headers,
        )
        self.assertEqual(response.data['status'], 'active')
        promo.refresh_from_db()
        self.assertFalse(promo.is_paused)

    def test_patch_rejects_non_settable_status(self):
        promo = _create_promotion()
        response = self.client.patch(
            _detail_url(promo.id), {'status': 'expired'}, format='json', **self.headers,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_patch_code_is_immutable(self):
        promo = _create_promotion()
        response = self.client.patch(
            _detail_url(promo.id), {'code': 'OTROCODIGO'}, format='json', **self.headers,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('code', _error_details(response))
        promo.refresh_from_db()
        self.assertEqual(promo.code, 'VERANO20')

    def test_patch_max_uses_below_current_uses_fails(self):
        promo = _create_promotion(max_uses=100, current_uses=10)
        response = self.client.patch(
            _detail_url(promo.id), {'max_uses': 5}, format='json', **self.headers,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('max_uses', _error_details(response))

    # ---- Delete ----

    def test_delete_without_redemptions(self):
        promo = _create_promotion()
        response = self.client.delete(_detail_url(promo.id), **self.headers)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Promotion.objects.filter(id=promo.id).exists())

    def test_delete_with_any_redemption_conflicts(self):
        # Cualquier canje bloquea (confirmado = historial; pending = pago en vuelo)
        for redemption_status in ['confirmed', 'pending', 'released']:
            promo = _create_promotion(code=f'DEL{redemption_status.upper()[:6]}')
            _create_redemption(promo, self.tenant, status=redemption_status)
            response = self.client.delete(_detail_url(promo.id), **self.headers)
            self.assertEqual(response.status_code, status.HTTP_409_CONFLICT, redemption_status)
            self.assertTrue(Promotion.objects.filter(id=promo.id).exists())

    # ---- Stats ----

    def test_stats_contract(self):
        promo = _create_promotion()
        other = _create_tenant('other-corp')
        _create_redemption(promo, self.tenant, status='confirmed', plan='starter')
        _create_redemption(promo, other, status='confirmed', plan='starter')
        _create_redemption(promo, other, status='confirmed', plan='professional',
                           original='79.00', discount='15.80', final='63.20')
        _create_redemption(promo, other, status='pending')
        _create_redemption(promo, other, status='released')

        response = self.client.get(_stats_url(promo.id), **self.headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['total_redemptions'], 5)
        self.assertEqual(response.data['confirmed'], 3)
        self.assertEqual(response.data['pending'], 1)
        self.assertEqual(response.data['released'], 1)
        self.assertAlmostEqual(response.data['total_discount'], 23.40)
        self.assertAlmostEqual(response.data['total_revenue'], 93.60)
        self.assertEqual(
            response.data['by_plan'],
            [{'plan': 'starter', 'count': 2}, {'plan': 'professional', 'count': 1}],
        )

    def test_stats_unknown_promotion_404(self):
        response = self.client.get(
            _stats_url('00000000-0000-0000-0000-000000000000'), **self.headers,
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class PromotionPermissionTests(APITestCase):
    """IsStaffUser y HasPermission son ambos load-bearing — se prueban por separado."""

    def setUp(self):
        cache.clear()
        self.tenant = _create_tenant('perm-corp')
        self.headers = {'HTTP_X_TENANT_SLUG': 'perm-corp'}

    def test_staff_without_permission_forbidden(self):
        staff = _create_staff(self.tenant, 'noperm@perm-corp.com')
        self.client.force_authenticate(user=staff)
        response = self.client.get(LIST_URL, **self.headers)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_non_staff_with_permission_forbidden(self):
        # Un Owner tenant-scoped tiene promotions.manage vía RBAC, pero NO es staff:
        # no debe poder tocar datos de plataforma.
        owner = User.objects.create_user(
            email='owner@perm-corp.com', name='Owner', password='pass123',
            tenant=self.tenant, is_staff=False,
        )
        _grant_permission(owner, 'promotions.manage')
        self.client.force_authenticate(user=owner)
        response = self.client.get(LIST_URL, **self.headers)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_anonymous_rejected(self):
        response = self.client.get(LIST_URL, **self.headers)
        self.assertIn(response.status_code,
                      (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_write_endpoints_also_gated(self):
        staff = _create_staff(self.tenant, 'noperm2@perm-corp.com')
        self.client.force_authenticate(user=staff)
        promo = _create_promotion()
        self.assertEqual(
            self.client.post(LIST_URL, _payload(), format='json', **self.headers).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.client.patch(_detail_url(promo.id), {'name': 'x'}, format='json',
                              **self.headers).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.client.delete(_detail_url(promo.id), **self.headers).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.client.get(_stats_url(promo.id), **self.headers).status_code,
            status.HTTP_403_FORBIDDEN,
        )
