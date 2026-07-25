"""
Tests de `activate_subscription_plan` — duración por ciclo y extensión del período.

Dos reglas que valen dinero (ADR-008, decisión 5):
  - la duración sale del ciclo pagado: 30 días mensual, 365 anual;
  - si el período vigente no venció, el nuevo se SUMA (pagar anticipado no pierde
    días); si ya venció, arranca en `now` (no se regala el tiempo impago).
"""
import uuid
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.subscriptions.models import Invoice, Subscription, YapePaymentProof
from apps.subscriptions.services import activate_subscription_plan, activate_yape_proof
from apps.tenants.models import Tenant

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']


def make_tenant(plan: str = 'free'):
    slug = f'tenant-{uuid.uuid4().hex[:8]}'
    return Tenant.objects.create(name=slug, slug=slug, subdomain=slug, plan=plan)


def make_subscription(plan: str = 'free', **extra) -> Subscription:
    """El signal ya creó la Subscription: se recupera y se ajusta."""
    tenant = make_tenant(plan)
    sub = Subscription.objects.get(tenant=tenant)
    sub.plan = plan
    for field, value in extra.items():
        setattr(sub, field, value)
    sub.save()
    return sub


def activate(sub, plan='professional', cycle='monthly', amount=Decimal('79.00')):
    return activate_subscription_plan(
        sub, plan, amount=amount,
        invoice_ref=f'test_{uuid.uuid4().hex[:8]}',
        billing_cycle=cycle,
    )


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS)
class TestPeriodDuration(TestCase):
    def test_monthly_gives_30_days(self):
        sub = make_subscription()
        activate(sub, cycle='monthly')

        sub.refresh_from_db()
        expected = timezone.now() + timedelta(days=30)
        self.assertAlmostEqual(
            sub.current_period_end, expected, delta=timedelta(minutes=5)
        )

    def test_annual_gives_365_days(self):
        sub = make_subscription()
        activate(sub, cycle='annual', amount=Decimal('854.00'))

        sub.refresh_from_db()
        expected = timezone.now() + timedelta(days=365)
        self.assertAlmostEqual(
            sub.current_period_end, expected, delta=timedelta(minutes=5)
        )

    def test_monthly_is_the_default_cycle(self):
        sub = make_subscription()
        activate_subscription_plan(
            sub, 'professional', amount=Decimal('79.00'), invoice_ref='test_default',
        )

        sub.refresh_from_db()
        self.assertEqual(sub.billing_cycle, 'monthly')
        self.assertAlmostEqual(
            sub.current_period_end,
            timezone.now() + timedelta(days=30),
            delta=timedelta(minutes=5),
        )

    def test_cycle_is_persisted(self):
        sub = make_subscription()
        activate(sub, cycle='annual')

        sub.refresh_from_db()
        self.assertEqual(sub.billing_cycle, 'annual')

    def test_unknown_cycle_raises(self):
        sub = make_subscription()
        with self.assertRaises(ValueError):
            activate(sub, cycle='quarterly')


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS)
class TestPeriodExtension(TestCase):
    def test_active_period_is_extended_not_replaced(self):
        """Renovar con 20 días restantes → 20 + 30 = 50 días."""
        remaining_end = timezone.now() + timedelta(days=20)
        sub = make_subscription('professional', current_period_end=remaining_end)

        activate(sub, cycle='monthly')

        sub.refresh_from_db()
        self.assertAlmostEqual(
            sub.current_period_end,
            remaining_end + timedelta(days=30),
            delta=timedelta(seconds=5),
        )

    def test_mid_period_upgrade_also_sums_remaining_days(self):
        """Upgrade de starter a professional a mitad de período: los días se suman."""
        remaining_end = timezone.now() + timedelta(days=20)
        sub = make_subscription('starter', current_period_end=remaining_end)

        activate(sub, plan='professional', cycle='monthly')

        sub.refresh_from_db()
        self.assertEqual(sub.plan, 'professional')
        self.assertAlmostEqual(
            sub.current_period_end,
            remaining_end + timedelta(days=30),
            delta=timedelta(seconds=5),
        )

    def test_annual_extends_from_current_end(self):
        remaining_end = timezone.now() + timedelta(days=10)
        sub = make_subscription('professional', current_period_end=remaining_end)

        activate(sub, cycle='annual')

        sub.refresh_from_db()
        self.assertAlmostEqual(
            sub.current_period_end,
            remaining_end + timedelta(days=365),
            delta=timedelta(seconds=5),
        )

    def test_extension_keeps_original_period_start(self):
        original_start = timezone.now() - timedelta(days=10)
        sub = make_subscription(
            'professional',
            current_period_start=original_start,
            current_period_end=timezone.now() + timedelta(days=20),
        )

        activate(sub, cycle='monthly')

        sub.refresh_from_db()
        self.assertAlmostEqual(
            sub.current_period_start, original_start, delta=timedelta(seconds=5)
        )

    def test_expired_period_starts_fresh_from_now(self):
        """No se regala el tiempo en que el servicio estuvo impago."""
        sub = make_subscription(
            'professional', current_period_end=timezone.now() - timedelta(days=40)
        )

        activate(sub, cycle='monthly')

        sub.refresh_from_db()
        self.assertAlmostEqual(
            sub.current_period_end,
            timezone.now() + timedelta(days=30),
            delta=timedelta(minutes=5),
        )
        self.assertAlmostEqual(
            sub.current_period_start, timezone.now(), delta=timedelta(minutes=5)
        )

    def test_null_period_starts_fresh_from_now(self):
        sub = make_subscription('free', current_period_end=None)

        activate(sub, cycle='monthly')

        sub.refresh_from_db()
        self.assertAlmostEqual(
            sub.current_period_end,
            timezone.now() + timedelta(days=30),
            delta=timedelta(minutes=5),
        )


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS)
class TestActivationClearsExpiryState(TestCase):
    def test_grace_and_reminders_are_reset(self):
        sub = make_subscription(
            'professional',
            status='past_due',
            grace_until=timezone.now() + timedelta(days=3),
            renewal_reminders_sent=['T-7', 'T-3'],
        )

        activate(sub, cycle='monthly')

        sub.refresh_from_db()
        self.assertEqual(sub.status, 'active')
        self.assertIsNone(sub.grace_until)
        self.assertEqual(sub.renewal_reminders_sent, [])

    def test_cancellation_is_revoked_by_paying(self):
        """
        Sin esto la tarea de expiración degradaría al tenant al llegar el período
        pese a haber pagado, porque `cancel_at_period_end` no admite gracia.
        """
        sub = make_subscription(
            'professional',
            cancel_at_period_end=True,
            current_period_end=timezone.now() + timedelta(days=5),
        )

        activate(sub, cycle='monthly')

        sub.refresh_from_db()
        self.assertFalse(sub.cancel_at_period_end)

    def test_trial_dates_are_cleared(self):
        sub = make_subscription(
            'professional',
            status='trialing',
            trial_start=timezone.now() - timedelta(days=5),
            trial_end=timezone.now() + timedelta(days=25),
        )

        activate(sub, cycle='monthly')

        sub.refresh_from_db()
        self.assertIsNone(sub.trial_start)
        self.assertIsNone(sub.trial_end)


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS)
class TestInvoicePeriod(TestCase):
    def test_new_period_invoice_starts_now(self):
        sub = make_subscription()

        invoice = activate(sub, cycle='monthly')

        self.assertAlmostEqual(
            invoice.period_start, timezone.now(), delta=timedelta(minutes=5)
        )
        self.assertEqual(invoice.status, 'paid')
        self.assertEqual(invoice.amount_cents, 7900)

    def test_extension_invoice_covers_only_the_purchased_span(self):
        """La factura empieza donde acababa el período anterior, no en `now`."""
        remaining_end = timezone.now() + timedelta(days=20)
        sub = make_subscription('professional', current_period_end=remaining_end)

        invoice = activate(sub, cycle='annual', amount=Decimal('854.00'))

        self.assertAlmostEqual(
            invoice.period_start, remaining_end, delta=timedelta(seconds=5)
        )
        self.assertAlmostEqual(
            invoice.period_end,
            remaining_end + timedelta(days=365),
            delta=timedelta(seconds=5),
        )
        self.assertEqual(invoice.amount_cents, 85400)


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS)
class TestYapeProofPropagatesCycle(TestCase):
    def _make_proof(self, cycle: str) -> YapePaymentProof:
        sub = make_subscription('free')
        return YapePaymentProof.objects.create(
            subscription=sub,
            screenshot='yape_proofs/test.png',
            plan='professional',
            billing_cycle=cycle,
            amount=Decimal('854.00') if cycle == 'annual' else Decimal('79.00'),
            admin_token=uuid.uuid4().hex,
        )

    def test_annual_proof_activates_365_days(self):
        proof = self._make_proof('annual')

        activate_yape_proof(proof)

        sub = Subscription.objects.get(pk=proof.subscription_id)
        self.assertEqual(sub.billing_cycle, 'annual')
        self.assertAlmostEqual(
            sub.current_period_end,
            timezone.now() + timedelta(days=365),
            delta=timedelta(minutes=5),
        )

    def test_monthly_proof_activates_30_days(self):
        proof = self._make_proof('monthly')

        activate_yape_proof(proof)

        sub = Subscription.objects.get(pk=proof.subscription_id)
        self.assertEqual(sub.billing_cycle, 'monthly')
        self.assertAlmostEqual(
            sub.current_period_end,
            timezone.now() + timedelta(days=30),
            delta=timedelta(minutes=5),
        )

    def test_proof_defaults_to_monthly(self):
        sub = make_subscription('free')
        proof = YapePaymentProof.objects.create(
            subscription=sub,
            screenshot='yape_proofs/test.png',
            plan='starter',
            amount=Decimal('19.00'),
            admin_token=uuid.uuid4().hex,
        )
        self.assertEqual(proof.billing_cycle, 'monthly')

        activate_yape_proof(proof)

        sub.refresh_from_db()
        self.assertAlmostEqual(
            sub.current_period_end,
            timezone.now() + timedelta(days=30),
            delta=timedelta(minutes=5),
        )
        self.assertEqual(Invoice.objects.filter(tenant=sub.tenant).count(), 1)
