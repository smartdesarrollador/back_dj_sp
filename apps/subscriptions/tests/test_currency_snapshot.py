"""
Tests del testigo histórico del cobro (tasa + importe en soles).

La regla que protegen todos: **lo que se guardó al pagar no se recalcula nunca**.
El cliente que paga por Yape transfiere soles; si la tasa se mueve entre el pago y
la aprobación, el importe del screenshot tiene que seguir cuadrando con lo que
muestra el panel.
"""
import uuid
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.subscriptions.models import CurrencyConfig, Subscription, PaymentProof
from apps.subscriptions.services import activate_subscription_plan, activate_payment_proof
from apps.tenants.models import Tenant
from utils.currency import capture_pen_snapshot

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


def _make_subscription(plan: str = 'free') -> Subscription:
    slug = f'tenant-{uuid.uuid4().hex[:8]}'
    tenant = Tenant.objects.create(name=slug, slug=slug, subdomain=slug, plan=plan)
    return Subscription.objects.get(tenant=tenant)


def _set_rate(value: str) -> None:
    """La tasa está cacheada 5 min: cambiarla exige invalidar."""
    cfg = CurrencyConfig.get()
    cfg.usd_to_pen = Decimal(value)
    cfg.save()


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestCaptureSnapshot(TestCase):
    def setUp(self):
        cache.clear()
        _set_rate('3.7500')

    def test_captures_rate_and_converted_amount(self):
        rate, amount_pen = capture_pen_snapshot(Decimal('199.00'))

        self.assertEqual(rate, Decimal('3.7500'))
        self.assertEqual(amount_pen, Decimal('746.25'))

    def test_rounds_half_up_like_the_client(self):
        # 15.20 × 3.3333 = 50.66616 → 50.67. Con otro redondeo el panel mostraría
        # un céntimo distinto del que el cliente vio y transfirió.
        _set_rate('3.3333')

        _, amount_pen = capture_pen_snapshot(Decimal('15.20'))

        self.assertEqual(amount_pen, Decimal('50.67'))

    def test_reflects_the_rate_in_force_at_call_time(self):
        _set_rate('4.2000')

        rate, amount_pen = capture_pen_snapshot(Decimal('79.00'))

        self.assertEqual(rate, Decimal('4.2000'))
        self.assertEqual(amount_pen, Decimal('331.80'))


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestSnapshotSurvivesRateChanges(TestCase):
    """El corazón de la feature."""

    def setUp(self):
        cache.clear()
        _set_rate('3.7500')

    def _make_proof(self, sub, amount=Decimal('199.00')):
        rate, amount_pen = capture_pen_snapshot(amount)
        return PaymentProof.objects.create(
            subscription=sub,
            screenshot='payment_proofs/x.png',
            plan='enterprise',
            billing_cycle='monthly',
            amount=amount,
            exchange_rate=rate,
            amount_pen=amount_pen,
            admin_token=uuid.uuid4().hex,
        )

    def test_invoice_inherits_the_rate_of_the_payment_not_of_today(self):
        sub = _make_subscription()
        proof = self._make_proof(sub)

        # La tasa sube ENTRE el pago y la aprobación — el caso que motiva la feature.
        _set_rate('4.2000')

        invoice = activate_payment_proof(proof)

        self.assertEqual(invoice.exchange_rate, Decimal('3.7500'))
        self.assertEqual(invoice.amount_pen_cents, 74625)
        # Y el cobro sigue siendo el mismo en la moneda base.
        self.assertEqual(invoice.amount_cents, 19900)
        self.assertEqual(invoice.currency, 'usd')

    def test_legacy_proof_yields_an_invoice_without_conversion(self):
        # Comprobante anterior al snapshot: no se le inventa la tasa de hoy.
        sub = _make_subscription()
        proof = PaymentProof.objects.create(
            subscription=sub,
            screenshot='payment_proofs/x.png',
            plan='professional',
            billing_cycle='monthly',
            amount=Decimal('79.00'),
            admin_token=uuid.uuid4().hex,
        )

        invoice = activate_payment_proof(proof)

        self.assertIsNone(invoice.exchange_rate)
        self.assertIsNone(invoice.amount_pen_cents)

    def test_activation_without_snapshot_leaves_it_empty(self):
        # Camino del cupón 100%: no hubo transferencia, así que no hay conversión.
        # Un S/ 0.00 sugeriría un cobro en soles de cero.
        sub = _make_subscription()

        invoice = activate_subscription_plan(
            sub, 'professional', amount=Decimal('0.00'), invoice_ref='promo_x',
        )

        self.assertIsNone(invoice.exchange_rate)
        self.assertIsNone(invoice.amount_pen_cents)

    def test_partial_snapshot_is_rejected(self):
        # Una tasa sin importe aparenta trazabilidad sin tenerla.
        sub = _make_subscription()

        with self.assertRaises(ValueError):
            activate_subscription_plan(
                sub, 'professional', amount=Decimal('79.00'), invoice_ref='x',
                exchange_rate=Decimal('3.7500'), amount_pen=None,
            )

    def test_invoice_exposes_a_readable_pen_amount(self):
        sub = _make_subscription()
        invoice = activate_payment_proof(self._make_proof(sub))

        self.assertEqual(invoice.amount_pen_display, 'S/ 746.25')

    def test_invoice_without_conversion_has_no_pen_display(self):
        sub = _make_subscription()
        invoice = activate_subscription_plan(
            sub, 'professional', amount=Decimal('79.00'), invoice_ref='x',
        )

        self.assertIsNone(invoice.amount_pen_display)


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestSnapshotInSerializers(TestCase):
    """El dato no sirve de nada si no llega a quien revisa el comprobante."""

    def setUp(self):
        cache.clear()
        _set_rate('3.7500')

    def test_invoice_serializer_exposes_the_conversion(self):
        from apps.subscriptions.serializers import InvoiceSerializer

        sub = _make_subscription()
        rate, amount_pen = capture_pen_snapshot(Decimal('199.00'))
        invoice = activate_subscription_plan(
            sub, 'enterprise', amount=Decimal('199.00'), invoice_ref='x',
            exchange_rate=rate, amount_pen=amount_pen,
        )

        data = InvoiceSerializer(invoice).data

        self.assertEqual(data['amount_pen'], 746.25)
        self.assertEqual(str(data['exchange_rate']), '3.7500')
        self.assertEqual(data['amount'], 199.0)

    def test_invoice_serializer_sends_null_when_there_was_no_conversion(self):
        from apps.subscriptions.serializers import InvoiceSerializer

        sub = _make_subscription()
        invoice = activate_subscription_plan(
            sub, 'professional', amount=Decimal('79.00'), invoice_ref='x',
        )

        data = InvoiceSerializer(invoice).data

        # `None`, no 0 ni la cadena 'None': el cliente no debe ver un S/ 0.00.
        self.assertIsNone(data['amount_pen'])
        self.assertIsNone(data['exchange_rate'])

    def test_proof_serializer_exposes_the_conversion(self):
        from apps.subscriptions.payment_admin_views import _serialize_proof

        sub = _make_subscription()
        rate, amount_pen = capture_pen_snapshot(Decimal('199.00'))
        proof = PaymentProof.objects.create(
            subscription=sub, screenshot='payment_proofs/x.png', plan='enterprise',
            billing_cycle='monthly', amount=Decimal('199.00'),
            exchange_rate=rate, amount_pen=amount_pen,
            admin_token=uuid.uuid4().hex,
        )

        data = _serialize_proof(proof)

        self.assertEqual(data['amount_pen'], '746.25')
        self.assertEqual(data['exchange_rate'], '3.7500')

    def test_proof_serializer_sends_null_for_legacy_proofs(self):
        from apps.subscriptions.payment_admin_views import _serialize_proof

        sub = _make_subscription()
        proof = PaymentProof.objects.create(
            subscription=sub, screenshot='payment_proofs/x.png', plan='professional',
            billing_cycle='monthly', amount=Decimal('79.00'),
            admin_token=uuid.uuid4().hex,
        )

        data = _serialize_proof(proof)

        self.assertIsNone(data['amount_pen'])
        self.assertIsNone(data['exchange_rate'])
