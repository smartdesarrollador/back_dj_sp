"""Tests for subscription billing models and signals."""
import uuid
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.subscriptions.models import Invoice, PaymentMethod, Subscription
from apps.tenants.models import Tenant

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']


def make_tenant(plan: str = 'free'):
    slug = f'tenant-{uuid.uuid4().hex[:8]}'
    return Tenant.objects.create(name=slug, slug=slug, subdomain=slug, plan=plan)


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS)
class TestSubscriptionSignal(TestCase):
    def test_subscription_created_on_tenant_creation(self):
        tenant = make_tenant()
        self.assertTrue(Subscription.objects.filter(tenant=tenant).exists())

    def test_subscription_has_trialing_status(self):
        tenant = make_tenant()
        sub = Subscription.objects.get(tenant=tenant)
        self.assertEqual(sub.status, 'trialing')

    def test_subscription_has_free_plan(self):
        tenant = make_tenant()
        sub = Subscription.objects.get(tenant=tenant)
        self.assertEqual(sub.plan, 'free')

    def test_subscription_trial_dates(self):
        tenant = make_tenant()
        sub = Subscription.objects.get(tenant=tenant)

        self.assertIsNotNone(sub.trial_start)
        self.assertIsNotNone(sub.trial_end)
        delta = sub.trial_end - sub.trial_start
        self.assertAlmostEqual(delta.days, 14, delta=1)

    def test_subscription_signal_is_idempotent(self):
        """get_or_create: repeated calls don't create duplicates."""
        tenant = make_tenant()
        Subscription.objects.get_or_create(
            tenant=tenant,
            defaults={'plan': 'free', 'status': 'trialing'},
        )
        self.assertEqual(Subscription.objects.filter(tenant=tenant).count(), 1)

    def test_subscription_str(self):
        tenant = make_tenant()
        sub = Subscription.objects.get(tenant=tenant)
        self.assertIn(tenant.slug, str(sub))
        self.assertIn('free', str(sub))


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS)
class TestPaymentMethodDefaultUniqueness(TestCase):
    def setUp(self):
        self.tenant = make_tenant()

    def _make_pm(self, is_default=False):
        return PaymentMethod.objects.create(
            tenant=self.tenant,
            stripe_payment_method_id=f'pm_{uuid.uuid4().hex[:16]}',
            brand='visa',
            last4='4242',
            is_default=is_default,
        )

    def test_payment_method_default_uniqueness(self):
        """Setting is_default on a new PM clears the flag from all others."""
        pm1 = self._make_pm(is_default=True)
        pm2 = self._make_pm(is_default=True)

        pm1.refresh_from_db()
        pm2.refresh_from_db()

        self.assertFalse(pm1.is_default, 'pm1 should no longer be default')
        self.assertTrue(pm2.is_default, 'pm2 should be the new default')

    def test_non_default_does_not_affect_others(self):
        pm1 = self._make_pm(is_default=True)
        pm2 = self._make_pm(is_default=False)

        pm1.refresh_from_db()
        self.assertTrue(pm1.is_default)
        self.assertFalse(pm2.is_default)

    def test_three_payment_methods_only_one_default(self):
        pm1 = self._make_pm(is_default=True)
        pm2 = self._make_pm(is_default=True)
        pm3 = self._make_pm(is_default=True)

        pm1.refresh_from_db()
        pm2.refresh_from_db()
        pm3.refresh_from_db()

        defaults = PaymentMethod.objects.filter(tenant=self.tenant, is_default=True)
        self.assertEqual(defaults.count(), 1)
        self.assertTrue(pm3.is_default)


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS)
class TestInvoiceAmountDisplay(TestCase):
    def setUp(self):
        self.tenant = make_tenant()

    def test_amount_display_from_cents(self):
        invoice = Invoice.objects.create(
            tenant=self.tenant,
            stripe_invoice_id=f'in_{uuid.uuid4().hex[:16]}',
            amount_cents=4999,
            status='paid',
        )
        self.assertEqual(invoice.amount_display, '$49.99')

    def test_amount_display_zero(self):
        invoice = Invoice.objects.create(
            tenant=self.tenant,
            stripe_invoice_id=f'in_{uuid.uuid4().hex[:16]}',
            amount_cents=0,
            status='draft',
        )
        self.assertEqual(invoice.amount_display, '$0.00')

    def test_amount_display_round_number(self):
        invoice = Invoice.objects.create(
            tenant=self.tenant,
            stripe_invoice_id=f'in_{uuid.uuid4().hex[:16]}',
            amount_cents=10000,
            status='paid',
        )
        self.assertEqual(invoice.amount_display, '$100.00')

    def test_invoice_str(self):
        invoice = Invoice.objects.create(
            tenant=self.tenant,
            stripe_invoice_id=f'in_{uuid.uuid4().hex[:16]}',
            amount_cents=500,
            status='open',
        )
        self.assertIn(self.tenant.slug, str(invoice))
        self.assertIn('$5.00', str(invoice))
