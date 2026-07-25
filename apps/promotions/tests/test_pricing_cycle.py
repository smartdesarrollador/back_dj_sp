"""
Tests del precio y el descuento por ciclo de facturación.

`get_plan_price(plan, cycle)` y `compute_discount(promotion, plan, cycle)` son la
única fuente del monto que se cobra, así que el ciclo tiene que llegar hasta el
precio: sin esto un cliente que elige "Anual" paga el precio mensual (o al revés).
Ver prd/features/renovacion-y-expiracion-de-planes.md y ADR-008, decisión 4.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.promotions.models import Promotion
from apps.promotions.services import compute_discount, get_plan_price
from apps.subscriptions.models import Plan


def _create_promotion(**overrides) -> Promotion:
    now = timezone.now()
    defaults = {
        'code': 'ANUAL20',
        'name': 'Promo Anual',
        'type': 'percentage',
        'value': Decimal('20'),
        'applicable_plans': ['starter', 'professional'],
        'starts_at': now - timedelta(days=1),
        'expires_at': now + timedelta(days=30),
    }
    defaults.update(overrides)
    return Promotion.objects.create(**defaults)


class TestGetPlanPriceByCycle(TestCase):
    def setUp(self):
        Plan.objects.create(
            id='professional', display_name='Professional',
            price_monthly=79, price_annual=854,
        )

    def test_monthly_is_the_default(self):
        self.assertEqual(get_plan_price('professional'), Decimal('79'))

    def test_monthly_explicit(self):
        self.assertEqual(get_plan_price('professional', 'monthly'), Decimal('79'))

    def test_annual_returns_full_year_price(self):
        """No la mensualidad equivalente: el precio del año completo."""
        self.assertEqual(get_plan_price('professional', 'annual'), Decimal('854'))

    def test_unknown_cycle_raises(self):
        for cycle in ['yearly', 'MONTHLY', 'quarterly', '']:
            with self.assertRaises(ValueError, msg=cycle):
                get_plan_price('professional', cycle)

    def test_unknown_plan_raises(self):
        with self.assertRaises(ValueError):
            get_plan_price('platinum', 'annual')


class TestGetPlanPriceCatalogFallback(TestCase):
    """Sin fila Plan en BD, los precios salen de PLAN_CATALOG (utils/plans.py)."""

    def test_annual_falls_back_to_catalog(self):
        self.assertFalse(Plan.objects.filter(id='starter').exists())
        self.assertEqual(get_plan_price('starter', 'annual'), Decimal('313'))

    def test_monthly_falls_back_to_catalog(self):
        self.assertEqual(get_plan_price('starter', 'monthly'), Decimal('29'))

    def test_db_row_wins_over_catalog(self):
        Plan.objects.create(
            id='starter', display_name='Starter', price_monthly=19, price_annual=200,
        )
        self.assertEqual(get_plan_price('starter', 'annual'), Decimal('200'))


class TestComputeDiscountByCycle(TestCase):
    def setUp(self):
        Plan.objects.create(
            id='professional', display_name='Professional',
            price_monthly=79, price_annual=854,
        )

    def test_percentage_applies_over_annual_price(self):
        amounts = compute_discount(_create_promotion(), 'professional', 'annual')
        self.assertEqual(amounts['original'], Decimal('854.00'))
        self.assertEqual(amounts['discount'], Decimal('170.80'))
        self.assertEqual(amounts['final'], Decimal('683.20'))

    def test_percentage_over_monthly_by_default(self):
        amounts = compute_discount(_create_promotion(), 'professional')
        self.assertEqual(amounts['original'], Decimal('79.00'))
        self.assertEqual(amounts['discount'], Decimal('15.80'))

    def test_fixed_amount_is_not_scaled_by_cycle(self):
        """Un cupón de $20 descuenta $20, sea mensual o anual."""
        promo = _create_promotion(type='fixed_amount', value=Decimal('20'))
        annual = compute_discount(promo, 'professional', 'annual')
        monthly = compute_discount(promo, 'professional', 'monthly')
        self.assertEqual(annual['discount'], Decimal('20.00'))
        self.assertEqual(monthly['discount'], Decimal('20.00'))
        self.assertEqual(annual['final'], Decimal('834.00'))

    def test_max_discount_caps_annual_percentage(self):
        promo = _create_promotion(value=Decimal('50'), max_discount=Decimal('100'))
        amounts = compute_discount(promo, 'professional', 'annual')
        self.assertEqual(amounts['discount'], Decimal('100.00'))
        self.assertEqual(amounts['final'], Decimal('754.00'))

    def test_fixed_amount_never_goes_below_zero_on_annual(self):
        promo = _create_promotion(type='fixed_amount', value=Decimal('9999'))
        amounts = compute_discount(promo, 'professional', 'annual')
        self.assertEqual(amounts['discount'], Decimal('854.00'))
        self.assertEqual(amounts['final'], Decimal('0.00'))

    def test_unknown_cycle_raises(self):
        with self.assertRaises(ValueError):
            compute_discount(_create_promotion(), 'professional', 'weekly')
