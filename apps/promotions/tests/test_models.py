"""
Tests del modelo Promotion — la propiedad computada `status` y sus prioridades:
paused > expired > depleted > active.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.promotions.models import Promotion


def _promotion(**overrides) -> Promotion:
    now = timezone.now()
    defaults = {
        'code': 'TESTCODE',
        'name': 'Test',
        'type': 'percentage',
        'value': Decimal('20'),
        'applicable_plans': ['starter'],
        'starts_at': now - timedelta(days=1),
        'expires_at': now + timedelta(days=30),
    }
    defaults.update(overrides)
    return Promotion.objects.create(**defaults)


class PromotionStatusTests(TestCase):
    def test_active_by_default(self):
        self.assertEqual(_promotion().status, 'active')

    def test_paused_wins_over_everything(self):
        promo = _promotion(
            is_paused=True,
            expires_at=timezone.now() - timedelta(days=1),  # también expirada
            max_uses=1, current_uses=1,                     # y agotada
        )
        self.assertEqual(promo.status, 'paused')

    def test_expired(self):
        promo = _promotion(
            starts_at=timezone.now() - timedelta(days=10),
            expires_at=timezone.now() - timedelta(days=1),
        )
        self.assertEqual(promo.status, 'expired')

    def test_depleted(self):
        promo = _promotion(max_uses=5, current_uses=5)
        self.assertEqual(promo.status, 'depleted')

    def test_unlimited_uses_never_depleted(self):
        promo = _promotion(max_uses=None, current_uses=9999)
        self.assertEqual(promo.status, 'active')

    def test_future_start_is_still_active_status(self):
        # El gate de canje por starts_at es del endpoint validate (Fase 2);
        # el status administrativo de una promo futura no pausada es 'active'.
        promo = _promotion(
            starts_at=timezone.now() + timedelta(days=5),
            expires_at=timezone.now() + timedelta(days=30),
        )
        self.assertEqual(promo.status, 'active')
