"""Unit tests for StripeClient — all Stripe API calls are mocked."""
import stripe as stripe_lib
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from apps.subscriptions.stripe_client import StripeClient


_TEST_STRIPE_SETTINGS = {
    'STRIPE_SECRET_KEY': 'sk_test_fake',
    'STRIPE_WEBHOOK_SECRET': 'whsec_fake',
    'STRIPE_PLAN_PRICES': {
        'starter': {
            'monthly': 'price_starter_monthly',
            'annual': 'price_starter_annual',
        },
        'professional': {
            'monthly': 'price_pro_monthly',
            'annual': 'price_pro_annual',
        },
        'enterprise': {
            'monthly': 'price_ent_monthly',
            'annual': 'price_ent_annual',
        },
    },
}


@override_settings(**_TEST_STRIPE_SETTINGS)
class TestStripeClient(TestCase):
    def setUp(self):
        self.client = StripeClient()

    # ─── create_customer ──────────────────────────────────────────────────────

    @patch('stripe.Customer.create')
    def test_create_customer_calls_stripe(self, mock_create):
        mock_create.return_value = MagicMock(id='cus_test123')
        tenant = MagicMock()
        tenant.name = 'Acme Corp'
        tenant.id = 'abc-uuid'
        tenant.slug = 'acme'
        tenant.users.filter.return_value.first.return_value = MagicMock(email='admin@acme.com')

        customer_id = self.client.create_customer(tenant)

        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args[1]
        self.assertEqual(call_kwargs['name'], 'Acme Corp')
        self.assertEqual(call_kwargs['email'], 'admin@acme.com')
        self.assertEqual(customer_id, 'cus_test123')

    @patch('stripe.Customer.create')
    def test_create_customer_no_users(self, mock_create):
        """Falls back gracefully when tenant has no users."""
        mock_create.return_value = MagicMock(id='cus_test456')
        tenant = MagicMock()
        tenant.name = 'Empty Tenant'
        tenant.id = 'def-uuid'
        tenant.slug = 'empty'
        tenant.users.filter.return_value.first.return_value = None
        tenant.users.first.return_value = None

        customer_id = self.client.create_customer(tenant)

        call_kwargs = mock_create.call_args[1]
        self.assertEqual(call_kwargs['email'], '')
        self.assertEqual(customer_id, 'cus_test456')

    # ─── create_subscription ──────────────────────────────────────────────────

    @patch('stripe.Subscription.create')
    def test_create_subscription_with_trial(self, mock_create):
        mock_sub = MagicMock(id='sub_test123', status='trialing')
        mock_create.return_value = mock_sub

        result = self.client.create_subscription('cus_123', 'price_123', trial_days=14)

        mock_create.assert_called_once_with(
            customer='cus_123',
            items=[{'price': 'price_123'}],
            trial_period_days=14,
            expand=['latest_invoice.payment_intent'],
        )
        self.assertEqual(result, mock_sub)

    @patch('stripe.Subscription.create')
    def test_create_subscription_no_trial(self, mock_create):
        mock_create.return_value = MagicMock(id='sub_test456')

        self.client.create_subscription('cus_123', 'price_123', trial_days=0)

        call_kwargs = mock_create.call_args[1]
        self.assertIsNone(call_kwargs['trial_period_days'])

    # ─── upgrade_subscription ─────────────────────────────────────────────────

    @patch('stripe.Subscription.modify')
    @patch('stripe.Subscription.retrieve')
    def test_upgrade_subscription_calls_modify(self, mock_retrieve, mock_modify):
        mock_retrieve.return_value = {
            'items': {'data': [{'id': 'si_existing_item'}]}
        }
        mock_modify.return_value = MagicMock(id='sub_123', status='active')

        self.client.upgrade_subscription('sub_123', 'price_new')

        mock_retrieve.assert_called_once_with('sub_123')
        mock_modify.assert_called_once_with(
            'sub_123',
            items=[{'id': 'si_existing_item', 'price': 'price_new'}],
            proration_behavior='create_prorations',
        )

    # ─── cancel_subscription ──────────────────────────────────────────────────

    @patch('stripe.Subscription.modify')
    def test_cancel_at_period_end(self, mock_modify):
        mock_modify.return_value = MagicMock(id='sub_123', cancel_at_period_end=True)

        self.client.cancel_subscription('sub_123', at_period_end=True)

        mock_modify.assert_called_once_with('sub_123', cancel_at_period_end=True)

    @patch('stripe.Subscription.modify')
    def test_cancel_immediately(self, mock_modify):
        mock_modify.return_value = MagicMock(id='sub_123', cancel_at_period_end=False)

        self.client.cancel_subscription('sub_123', at_period_end=False)

        mock_modify.assert_called_once_with('sub_123', cancel_at_period_end=False)

    # ─── construct_webhook_event ──────────────────────────────────────────────

    @patch('stripe.Webhook.construct_event')
    def test_construct_webhook_event_invalid_sig_raises(self, mock_construct):
        mock_construct.side_effect = stripe_lib.error.SignatureVerificationError(
            'Invalid signature', 'sig'
        )

        with self.assertRaises(stripe_lib.error.SignatureVerificationError):
            self.client.construct_webhook_event(b'payload', 'invalid_sig')

    @patch('stripe.Webhook.construct_event')
    def test_construct_webhook_event_success(self, mock_construct):
        mock_event = {'type': 'invoice.payment_succeeded', 'data': {}}
        mock_construct.return_value = mock_event

        result = self.client.construct_webhook_event(b'payload', 'valid_sig')

        mock_construct.assert_called_once_with(b'payload', 'valid_sig', 'whsec_fake')
        self.assertEqual(result, mock_event)

    # ─── get_price_id ─────────────────────────────────────────────────────────

    def test_get_price_id_returns_correct_id(self):
        self.assertEqual(
            self.client.get_price_id('starter', 'monthly'),
            'price_starter_monthly',
        )

    def test_get_price_id_annual(self):
        self.assertEqual(
            self.client.get_price_id('professional', 'annual'),
            'price_pro_annual',
        )

    def test_get_price_id_unknown_plan_returns_empty(self):
        self.assertEqual(self.client.get_price_id('unknown', 'monthly'), '')

    def test_get_price_id_unknown_cycle_returns_empty(self):
        self.assertEqual(self.client.get_price_id('starter', 'weekly'), '')
