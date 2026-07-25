"""
Tests del comando `backfill_subscription_periods`.

Cubre el alcance acotado del backfill (solo `status='active'` con plan de pago) y
su idempotencia. Ver ADR-008 y la sección "Migración de Datos Existentes" del PRD.

Nota: el signal `auto_create_subscription` ya crea la Subscription al crear el
Tenant, así que los tests la recuperan y la actualizan — nunca la crean.
"""
import uuid
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.subscriptions.models import Invoice, Subscription
from apps.tenants.models import Tenant

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']


def make_tenant(plan: str = 'free'):
    slug = f'tenant-{uuid.uuid4().hex[:8]}'
    return Tenant.objects.create(name=slug, slug=slug, subdomain=slug, plan=plan)


def make_subscription(plan: str, status: str, **extra) -> Subscription:
    """Deja la suscripción del tenant en el estado buscado, sin período."""
    tenant = make_tenant(plan)
    sub = Subscription.objects.get(tenant=tenant)
    sub.plan = plan
    sub.status = status
    sub.current_period_start = None
    sub.current_period_end = None
    for field, value in extra.items():
        setattr(sub, field, value)
    sub.save()
    return sub


def run_backfill(apply_changes: bool = False) -> str:
    out = StringIO()
    args = ['backfill_subscription_periods']
    if apply_changes:
        args.append('--apply')
    call_command(*args, stdout=out)
    return out.getvalue()


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS)
class TestBackfillSubscriptionPeriods(TestCase):
    def test_active_paid_without_invoice_gets_courtesy_period(self):
        sub = make_subscription('professional', 'active')

        run_backfill(apply_changes=True)

        sub.refresh_from_db()
        self.assertIsNotNone(sub.current_period_end)
        expected = timezone.now() + timedelta(days=30)
        self.assertAlmostEqual(
            sub.current_period_end, expected, delta=timedelta(minutes=5)
        )
        self.assertIsNotNone(sub.current_period_start)

    def test_active_paid_with_paid_invoice_inherits_its_period(self):
        sub = make_subscription('starter', 'active')
        period_start = timezone.now() - timedelta(days=10)
        period_end = timezone.now() + timedelta(days=20)
        Invoice.objects.create(
            tenant=sub.tenant,
            stripe_invoice_id=f'inv_{uuid.uuid4().hex[:8]}',
            amount_cents=900,
            status='paid',
            period_start=period_start,
            period_end=period_end,
            invoice_date=period_start,
        )

        run_backfill(apply_changes=True)

        sub.refresh_from_db()
        self.assertAlmostEqual(
            sub.current_period_end, period_end, delta=timedelta(seconds=1)
        )
        self.assertAlmostEqual(
            sub.current_period_start, period_start, delta=timedelta(seconds=1)
        )

    def test_expired_paid_invoice_is_still_used(self):
        """Un período vencido se registra tal cual: el tenant entrará en gracia."""
        sub = make_subscription('professional', 'active')
        period_end = timezone.now() - timedelta(days=40)
        Invoice.objects.create(
            tenant=sub.tenant,
            stripe_invoice_id=f'inv_{uuid.uuid4().hex[:8]}',
            amount_cents=3900,
            status='paid',
            period_start=period_end - timedelta(days=30),
            period_end=period_end,
            invoice_date=period_end,
        )

        run_backfill(apply_changes=True)

        sub.refresh_from_db()
        self.assertLess(sub.current_period_end, timezone.now())

    def test_unpaid_invoice_is_ignored(self):
        """Una factura no pagada no define período: cae en cortesía."""
        sub = make_subscription('starter', 'active')
        Invoice.objects.create(
            tenant=sub.tenant,
            stripe_invoice_id=f'inv_{uuid.uuid4().hex[:8]}',
            amount_cents=900,
            status='open',
            period_start=timezone.now() - timedelta(days=200),
            period_end=timezone.now() - timedelta(days=170),
            invoice_date=timezone.now() - timedelta(days=200),
        )

        run_backfill(apply_changes=True)

        sub.refresh_from_db()
        self.assertGreater(sub.current_period_end, timezone.now())

    def test_trialing_is_untouched(self):
        sub = make_subscription('professional', 'trialing')

        run_backfill(apply_changes=True)

        sub.refresh_from_db()
        self.assertIsNone(sub.current_period_end)

    def test_pending_payment_and_unpaid_are_untouched(self):
        pending = make_subscription('professional', 'pending_payment')
        unpaid = make_subscription('starter', 'unpaid')

        run_backfill(apply_changes=True)

        pending.refresh_from_db()
        unpaid.refresh_from_db()
        self.assertIsNone(pending.current_period_end)
        self.assertIsNone(unpaid.current_period_end)

    def test_canceled_is_untouched(self):
        sub = make_subscription('enterprise', 'canceled')

        run_backfill(apply_changes=True)

        sub.refresh_from_db()
        self.assertIsNone(sub.current_period_end)

    def test_free_plan_is_untouched(self):
        sub = make_subscription('free', 'active')

        run_backfill(apply_changes=True)

        sub.refresh_from_db()
        self.assertIsNone(sub.current_period_end)

    def test_existing_period_is_not_overwritten(self):
        sub = make_subscription('professional', 'active')
        original_end = timezone.now() + timedelta(days=3)
        sub.current_period_end = original_end
        sub.save(update_fields=['current_period_end'])

        run_backfill(apply_changes=True)

        sub.refresh_from_db()
        self.assertAlmostEqual(
            sub.current_period_end, original_end, delta=timedelta(seconds=1)
        )

    def test_dry_run_does_not_write(self):
        sub = make_subscription('professional', 'active')

        output = run_backfill(apply_changes=False)

        sub.refresh_from_db()
        self.assertIsNone(sub.current_period_end)
        self.assertIn('DRY-RUN', output)
        self.assertIn('Se actualizarían', output)

    def test_is_idempotent(self):
        sub = make_subscription('professional', 'active')

        run_backfill(apply_changes=True)
        sub.refresh_from_db()
        first_end = sub.current_period_end

        second_output = run_backfill(apply_changes=True)

        sub.refresh_from_db()
        self.assertEqual(sub.current_period_end, first_end)
        self.assertIn('Nada que rellenar', second_output)

    def test_reports_source_per_subscription(self):
        make_subscription('professional', 'active')
        with_invoice = make_subscription('starter', 'active')
        Invoice.objects.create(
            tenant=with_invoice.tenant,
            stripe_invoice_id=f'inv_{uuid.uuid4().hex[:8]}',
            amount_cents=900,
            status='paid',
            period_start=timezone.now(),
            period_end=timezone.now() + timedelta(days=30),
            invoice_date=timezone.now(),
        )

        output = run_backfill(apply_changes=False)

        self.assertIn('cortesia', output)
        self.assertIn('invoice', output)
        self.assertIn('1 desde factura pagada', output)
        self.assertIn('1 con período de cortesía', output)


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS)
class TestBackfillNoop(TestCase):
    def test_reports_nothing_to_do_when_all_have_periods(self):
        make_subscription('free', 'active')
        make_subscription('professional', 'trialing')

        output = run_backfill(apply_changes=False)

        self.assertIn('Nada que rellenar', output)
