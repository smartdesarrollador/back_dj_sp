"""
Tests del endpoint público POST /api/v1/public/promotions/validate/.

Covers: cálculo por tipo (percentage con cap max_discount, fixed_amount con
piso $0), conversión a PEN vía YapeConfig, normalización case-insensitive,
respuesta opaca 'invalid' (inexistente / pausada / aún no vigente) y razones
específicas (expired / depleted / plan_not_applicable). Siempre 200 con
valid: false — nunca 404.
"""
from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.promotions.models import Promotion
from apps.subscriptions.models import Plan, YapeConfig

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

VALIDATE_URL = '/api/v1/public/promotions/validate/'


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


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class PromotionValidateTests(APITestCase):
    def setUp(self):
        cache.clear()
        Plan.objects.create(id='starter', display_name='Starter', price_monthly=19)
        cfg = YapeConfig.get()
        cfg.exchange_rate = Decimal('3.75')
        cfg.save(update_fields=['exchange_rate'])

    def _validate(self, code='VERANO20', plan='starter'):
        return self.client.post(VALIDATE_URL, {'code': code, 'plan': plan}, format='json')

    def test_valid_percentage_with_pen_conversion(self):
        _create_promotion()
        response = self._validate()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {
            'valid': True,
            'code': 'VERANO20',
            'type': 'percentage',
            'value': 20.0,
            'original_price': 19.0,
            'discount_amount': 3.8,
            'final_price': 15.2,
            'exchange_rate': '3.75',
            'final_price_pen': 57.0,
        })

    def test_code_is_case_insensitive(self):
        _create_promotion()
        response = self._validate(code='  verano20 ')
        self.assertTrue(response.data['valid'])
        self.assertEqual(response.data['code'], 'VERANO20')

    def test_percentage_respects_max_discount_cap(self):
        _create_promotion(value=Decimal('50'), max_discount=Decimal('5'))
        response = self._validate()
        self.assertEqual(response.data['discount_amount'], 5.0)
        self.assertEqual(response.data['final_price'], 14.0)

    def test_fixed_amount(self):
        _create_promotion(type='fixed_amount', value=Decimal('4'))
        response = self._validate()
        self.assertEqual(response.data['discount_amount'], 4.0)
        self.assertEqual(response.data['final_price'], 15.0)

    def test_fixed_amount_floors_at_zero(self):
        _create_promotion(type='fixed_amount', value=Decimal('500'))
        response = self._validate()
        self.assertEqual(response.data['discount_amount'], 19.0)
        self.assertEqual(response.data['final_price'], 0.0)

    def test_hundred_percent_gives_zero_final(self):
        _create_promotion(value=Decimal('100'))
        response = self._validate()
        self.assertEqual(response.data['final_price'], 0.0)

    def test_plan_price_falls_back_to_catalog(self):
        # Sin fila Plan para professional → PLAN_CATALOG (79 USD)
        _create_promotion(applicable_plans=['professional'])
        response = self._validate(plan='professional')
        self.assertEqual(response.data['original_price'], 79.0)

    def test_unknown_code_is_opaque_invalid(self):
        response = self._validate(code='NOEXISTE')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {'valid': False, 'reason': 'invalid'})

    def test_paused_and_not_started_are_also_opaque_invalid(self):
        _create_promotion(code='PAUSADA1', is_paused=True)
        _create_promotion(code='FUTURA01', starts_at=timezone.now() + timedelta(days=5))
        for code in ['PAUSADA1', 'FUTURA01']:
            response = self._validate(code=code)
            self.assertEqual(response.data, {'valid': False, 'reason': 'invalid'}, code)

    def test_expired(self):
        _create_promotion(
            starts_at=timezone.now() - timedelta(days=30),
            expires_at=timezone.now() - timedelta(days=1),
        )
        response = self._validate()
        self.assertEqual(response.data, {'valid': False, 'reason': 'expired'})

    def test_depleted(self):
        _create_promotion(max_uses=3, current_uses=3)
        response = self._validate()
        self.assertEqual(response.data, {'valid': False, 'reason': 'depleted'})

    def test_plan_not_applicable(self):
        _create_promotion(applicable_plans=['professional'])
        response = self._validate(plan='starter')
        self.assertEqual(response.data, {'valid': False, 'reason': 'plan_not_applicable'})

    def test_invalid_plan_is_400(self):
        for plan in ['free', 'premium', '']:
            response = self._validate(plan=plan)
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, plan)

    def test_empty_code_is_invalid(self):
        response = self._validate(code='   ')
        self.assertEqual(response.data, {'valid': False, 'reason': 'invalid'})
