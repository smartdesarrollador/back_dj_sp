"""Tests for subscription billing views. All Stripe API calls are mocked."""
import base64
import json
import uuid
from unittest.mock import MagicMock, patch

import stripe as stripe_lib
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.subscriptions.models import Invoice, PaymentMethod, Subscription
from apps.tenants.models import Tenant

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']

# Valid Fernet key: base64url-encoded 32 bytes
_TEST_ENCRYPTION_KEY = base64.urlsafe_b64encode(b'testkey-for-paso24-encryption-!1').decode()

_TEST_STRIPE_SETTINGS = {
    'STRIPE_SECRET_KEY': 'sk_test_fake',
    'STRIPE_WEBHOOK_SECRET': 'whsec_fake',
    'STRIPE_PLAN_PRICES': {
        'starter': {'monthly': 'price_starter_monthly', 'annual': 'price_starter_annual'},
        'professional': {'monthly': 'price_pro_monthly', 'annual': 'price_pro_annual'},
        'enterprise': {'monthly': 'price_ent_monthly', 'annual': 'price_ent_annual'},
    },
}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def make_tenant(plan: str = 'free'):
    slug = f'tenant-{uuid.uuid4().hex[:8]}'
    return Tenant.objects.create(name=slug, slug=slug, subdomain=slug, plan=plan)


def make_user(tenant, email=None, is_superuser=False):
    from apps.auth_app.models import User
    email = email or f'user-{uuid.uuid4().hex[:8]}@example.com'
    return User.objects.create_user(
        email=email,
        name='Test User',
        password='testpass123',
        tenant=tenant,
        is_superuser=is_superuser,
        is_staff=is_superuser,
    )


def slug_header(slug: str) -> dict:
    return {'HTTP_X_TENANT_SLUG': slug}


# ─── CurrentSubscriptionView ──────────────────────────────────────────────────

@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, **_TEST_STRIPE_SETTINGS)
class TestCurrentSubscriptionView(APITestCase):
    def setUp(self):
        self.tenant = make_tenant()
        self.user = make_user(self.tenant, is_superuser=True)
        self.client.force_authenticate(user=self.user)

    def test_current_subscription_requires_auth(self):
        client = APIClient()
        resp = client.get(
            '/api/v1/admin/subscriptions/current',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_current_subscription_creates_if_not_exists(self):
        # Remove auto-created subscription from signal
        Subscription.objects.filter(tenant=self.tenant).delete()

        resp = self.client.get(
            '/api/v1/admin/subscriptions/current',
            **slug_header(self.tenant.slug),
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('subscription', resp.data)
        self.assertTrue(Subscription.objects.filter(tenant=self.tenant).exists())

    def test_current_subscription_returns_existing(self):
        sub = Subscription.objects.filter(tenant=self.tenant).first()
        if not sub:
            sub = Subscription.objects.create(
                tenant=self.tenant, plan='starter', status='active'
            )
        else:
            sub.plan = 'starter'
            sub.status = 'active'
            sub.save()

        resp = self.client.get(
            '/api/v1/admin/subscriptions/current',
            **slug_header(self.tenant.slug),
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['subscription']['plan'], 'starter')

    def test_current_subscription_includes_usage(self):
        resp = self.client.get(
            '/api/v1/admin/subscriptions/current',
            **slug_header(self.tenant.slug),
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('usage', resp.data['subscription'])
        usage = resp.data['subscription']['usage']
        self.assertIn('users', usage)
        self.assertIn('storage', usage)
        self.assertIn('api_calls', usage)


# ─── UpgradeSubscriptionView ──────────────────────────────────────────────────

@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, **_TEST_STRIPE_SETTINGS)
class TestUpgradeSubscriptionView(APITestCase):
    def setUp(self):
        self.tenant = make_tenant(plan='free')
        self.user = make_user(self.tenant, is_superuser=True)
        self.client.force_authenticate(user=self.user)
        # Signal already created a Subscription; ensure we have a reference
        self.sub = Subscription.objects.get(tenant=self.tenant)

    def test_upgrade_requires_auth(self):
        client = APIClient()
        resp = client.post(
            '/api/v1/admin/subscriptions/upgrade',
            {'new_plan': 'starter', 'billing_cycle': 'monthly'},
            format='json',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_upgrade_requires_permission(self):
        # Non-superuser without RBAC permission should get 403
        user_no_perm = make_user(self.tenant)
        self.client.force_authenticate(user=user_no_perm)

        resp = self.client.post(
            '/api/v1/admin/subscriptions/upgrade',
            {'new_plan': 'starter', 'billing_cycle': 'monthly'},
            format='json',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    @patch('stripe.Customer.create')
    @patch('stripe.Subscription.create')
    def test_upgrade_plan_success(self, mock_sub_create, mock_cust_create):
        mock_cust_create.return_value = MagicMock(id='cus_test_upgrade')
        mock_sub_create.return_value = {'id': 'sub_test_upgrade', 'status': 'active'}

        resp = self.client.post(
            '/api/v1/admin/subscriptions/upgrade',
            {'new_plan': 'starter', 'billing_cycle': 'monthly'},
            format='json',
            **slug_header(self.tenant.slug),
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.plan, 'starter')
        self.assertEqual(self.sub.stripe_subscription_id, 'sub_test_upgrade')

    def test_upgrade_same_plan_rejected(self):
        # Set tenant plan to starter first
        self.tenant.plan = 'starter'
        self.tenant.save()

        resp = self.client.post(
            '/api/v1/admin/subscriptions/upgrade',
            {'new_plan': 'starter', 'billing_cycle': 'monthly'},
            format='json',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_upgrade_invalid_plan_rejected(self):
        resp = self.client.post(
            '/api/v1/admin/subscriptions/upgrade',
            {'new_plan': 'invalid_plan', 'billing_cycle': 'monthly'},
            format='json',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


# ─── CancelSubscriptionView ───────────────────────────────────────────────────

@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, **_TEST_STRIPE_SETTINGS)
class TestCancelSubscriptionView(APITestCase):
    def setUp(self):
        self.tenant = make_tenant()
        self.user = make_user(self.tenant, is_superuser=True)
        self.client.force_authenticate(user=self.user)
        self.sub = Subscription.objects.get(tenant=self.tenant)
        self.sub.plan = 'starter'
        self.sub.status = 'active'
        self.sub.stripe_subscription_id = 'sub_cancel_test'
        self.sub.stripe_customer_id = 'cus_cancel_test'
        self.sub.save()

    @patch('stripe.Subscription.modify')
    def test_cancel_subscription_sets_flag(self, mock_modify):
        mock_modify.return_value = MagicMock(id='sub_cancel_test', cancel_at_period_end=True)

        resp = self.client.post(
            '/api/v1/admin/subscriptions/cancel',
            format='json',
            **slug_header(self.tenant.slug),
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.sub.refresh_from_db()
        self.assertTrue(self.sub.cancel_at_period_end)

    def test_cancel_requires_auth(self):
        client = APIClient()
        resp = client.post(
            '/api/v1/admin/subscriptions/cancel',
            format='json',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_cancel_requires_permission(self):
        user_no_perm = make_user(self.tenant)
        self.client.force_authenticate(user=user_no_perm)

        resp = self.client.post(
            '/api/v1/admin/subscriptions/cancel',
            format='json',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_cancel_no_stripe_id_updates_flag_only(self):
        """If no stripe_subscription_id, just sets cancel_at_period_end."""
        self.sub.stripe_subscription_id = ''
        self.sub.save()

        resp = self.client.post(
            '/api/v1/admin/subscriptions/cancel',
            format='json',
            **slug_header(self.tenant.slug),
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.sub.refresh_from_db()
        self.assertTrue(self.sub.cancel_at_period_end)


# ─── InvoiceListView ──────────────────────────────────────────────────────────

@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, **_TEST_STRIPE_SETTINGS)
class TestInvoiceListView(APITestCase):
    def setUp(self):
        self.tenant = make_tenant()
        self.other_tenant = make_tenant()
        self.user = make_user(self.tenant, is_superuser=True)
        self.client.force_authenticate(user=self.user)

    def test_invoice_list_returns_tenant_invoices(self):
        Invoice.objects.create(
            tenant=self.tenant,
            stripe_invoice_id=f'in_{uuid.uuid4().hex}',
            amount_cents=1000,
            status='paid',
        )
        Invoice.objects.create(
            tenant=self.other_tenant,
            stripe_invoice_id=f'in_{uuid.uuid4().hex}',
            amount_cents=2000,
            status='paid',
        )

        resp = self.client.get(
            '/api/v1/admin/billing/invoices',
            **slug_header(self.tenant.slug),
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data['invoices']), 1)
        self.assertEqual(resp.data['invoices'][0]['amount_cents'], 1000)

    def test_invoice_list_requires_auth(self):
        client = APIClient()
        resp = client.get(
            '/api/v1/admin/billing/invoices',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_invoice_list_empty(self):
        resp = self.client.get(
            '/api/v1/admin/billing/invoices',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['invoices'], [])


# ─── WebhookView ──────────────────────────────────────────────────────────────

@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, **_TEST_STRIPE_SETTINGS)
class TestWebhookView(APITestCase):
    def setUp(self):
        self.tenant = make_tenant()
        self.sub = Subscription.objects.get(tenant=self.tenant)
        self.sub.plan = 'starter'
        self.sub.status = 'active'
        self.sub.stripe_subscription_id = 'sub_wh_test'
        self.sub.stripe_customer_id = 'cus_wh_test'
        self.sub.save()

    def _post_webhook(self, event_dict, sig='t=123,v1=abc'):
        return self.client.post(
            '/api/v1/admin/billing/webhooks',
            data=json.dumps(event_dict),
            content_type='application/json',
            HTTP_STRIPE_SIGNATURE=sig,
        )

    @patch('stripe.Webhook.construct_event')
    def test_webhook_payment_succeeded_marks_paid(self, mock_construct):
        invoice = Invoice.objects.create(
            tenant=self.tenant,
            stripe_invoice_id='in_wh_paid',
            amount_cents=999,
            status='open',
        )
        mock_construct.return_value = {
            'type': 'invoice.payment_succeeded',
            'data': {
                'object': {
                    'id': 'in_wh_paid',
                    'subscription': 'sub_wh_test',
                }
            },
        }

        resp = self._post_webhook({'type': 'invoice.payment_succeeded'})

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, 'paid')
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, 'active')

    @patch('stripe.Webhook.construct_event')
    def test_webhook_payment_failed_marks_past_due(self, mock_construct):
        invoice = Invoice.objects.create(
            tenant=self.tenant,
            stripe_invoice_id='in_wh_failed',
            amount_cents=999,
            status='open',
        )
        mock_construct.return_value = {
            'type': 'invoice.payment_failed',
            'data': {
                'object': {
                    'id': 'in_wh_failed',
                    'subscription': 'sub_wh_test',
                }
            },
        }

        resp = self._post_webhook({'type': 'invoice.payment_failed'})

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, 'past_due')

    @patch('stripe.Webhook.construct_event')
    def test_webhook_invalid_signature_returns_400(self, mock_construct):
        mock_construct.side_effect = stripe_lib.error.SignatureVerificationError(
            'Invalid', 'sig'
        )

        resp = self._post_webhook({'type': 'test'}, sig='invalid')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    @patch('stripe.Webhook.construct_event')
    def test_webhook_subscription_deleted_resets_plan(self, mock_construct):
        mock_construct.return_value = {
            'type': 'customer.subscription.deleted',
            'data': {
                'object': {'id': 'sub_wh_test'}
            },
        }

        resp = self._post_webhook({'type': 'customer.subscription.deleted'})

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, 'canceled')
        self.assertEqual(self.sub.plan, 'free')
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.plan, 'free')

    @patch('stripe.Webhook.construct_event')
    def test_webhook_unknown_event_returns_200(self, mock_construct):
        """Unknown event types are ignored but return 200."""
        mock_construct.return_value = {
            'type': 'some.unknown.event',
            'data': {'object': {}},
        }

        resp = self._post_webhook({'type': 'some.unknown.event'})

        self.assertEqual(resp.status_code, status.HTTP_200_OK)


# ─── PaymentMethodListView / PaymentMethodDetailView ──────────────────────────

_PM_URL = '/api/v1/admin/billing/payment-methods'


@override_settings(
    PASSWORD_HASHERS=_FAST_HASHERS,
    ENCRYPTION_KEY=_TEST_ENCRYPTION_KEY,
    **_TEST_STRIPE_SETTINGS,
)
class TestPaymentMethodCRUD(APITestCase):
    def setUp(self):
        self.tenant = make_tenant()
        self.other_tenant = make_tenant()
        self.user = make_user(self.tenant, is_superuser=True)
        self.other_user = make_user(self.other_tenant, is_superuser=True)
        self.client.force_authenticate(user=self.user)

    def _pm_detail_url(self, pm_id):
        return f'{_PM_URL}/{pm_id}/'

    def _make_latam_pm(self, tenant=None, **kwargs):
        """Create a LATAM PaymentMethod directly in the DB (bypasses save encryption)."""
        import os
        os.environ.setdefault('ENCRYPTION_KEY', _TEST_ENCRYPTION_KEY)
        t = tenant or self.tenant
        return PaymentMethod.objects.create(
            tenant=t,
            type='external',
            external_type=kwargs.get('external_type', 'paypal'),
            external_email=kwargs.get('external_email', 'test@paypal.com'),
            is_default=kwargs.get('is_default', False),
        )

    # ── List ──────────────────────────────────────────────────────────────────

    def test_list_returns_payment_methods(self):
        self._make_latam_pm()
        resp = self.client.get(_PM_URL, **slug_header(self.tenant.slug))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data['payment_methods']), 1)

    def test_list_requires_auth(self):
        client = APIClient()
        resp = client.get(_PM_URL, **slug_header(self.tenant.slug))
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_list_tenant_isolation(self):
        self._make_latam_pm(tenant=self.other_tenant)
        resp = self.client.get(_PM_URL, **slug_header(self.tenant.slug))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['payment_methods'], [])

    # ── Create (LATAM) ────────────────────────────────────────────────────────

    def test_create_latam_paypal(self):
        resp = self.client.post(
            _PM_URL,
            {'external_type': 'paypal', 'external_email': 'user@paypal.com', 'is_default': True},
            format='json',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        pm_data = resp.data['payment_method']
        self.assertEqual(pm_data['external_type'], 'paypal')
        self.assertEqual(pm_data['external_email'], 'user@paypal.com')
        self.assertEqual(pm_data['type'], 'external')

    def test_create_latam_yape(self):
        resp = self.client.post(
            _PM_URL,
            {'external_type': 'yape', 'external_phone': '+51999888777', 'is_default': False},
            format='json',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['payment_method']['external_type'], 'yape')
        self.assertEqual(resp.data['payment_method']['external_phone'], '+51999888777')

    def test_create_encrypts_account_id(self):
        resp = self.client.post(
            _PM_URL,
            {
                'external_type': 'mercadopago',
                'external_email': 'vendor@mp.com',
                'external_account_id': 'acc-secret-123',
                'is_default': True,
            },
            format='json',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        pm_id = resp.data['payment_method']['id']
        pm = PaymentMethod.objects.get(id=pm_id)
        # Stored value must be encrypted (Fernet tokens start with 'gAAAAA')
        self.assertTrue(pm.external_account_id.startswith('gAAAAA'))
        # Must not be returned in response
        self.assertNotIn('external_account_id', resp.data['payment_method'])

    def test_create_missing_method_returns_400(self):
        resp = self.client.post(
            _PM_URL,
            {'is_default': True},
            format='json',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_both_methods_rejected(self):
        resp = self.client.post(
            _PM_URL,
            {
                'stripe_payment_method_id': 'pm_fake',
                'external_type': 'paypal',
                'external_email': 'x@y.com',
            },
            format='json',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    # ── Create (Stripe card) ───────────────────────────────────────────────────

    @patch('stripe.Customer.create')
    @patch('stripe.PaymentMethod.attach')
    @patch('stripe.Customer.modify')
    @patch('stripe.PaymentMethod.retrieve')
    def test_create_card_via_stripe(
        self, mock_retrieve, mock_modify, mock_attach, mock_cust_create
    ):
        mock_cust_create.return_value = MagicMock(id='cus_pm_test')
        mock_attach.return_value = MagicMock()
        mock_modify.return_value = MagicMock()
        mock_retrieve.return_value = {
            'type': 'card',
            'card': {'brand': 'visa', 'last4': '4242', 'exp_month': 12, 'exp_year': 2030},
        }

        resp = self.client.post(
            _PM_URL,
            {'stripe_payment_method_id': 'pm_card_test', 'set_default': True},
            format='json',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        pm_data = resp.data['payment_method']
        self.assertEqual(pm_data['brand'], 'visa')
        self.assertEqual(pm_data['last4'], '4242')

    # ── PATCH ─────────────────────────────────────────────────────────────────

    def test_patch_set_default(self):
        pm1 = self._make_latam_pm(external_type='paypal', is_default=True)
        pm2 = self._make_latam_pm(external_type='yape', is_default=False)

        resp = self.client.patch(
            self._pm_detail_url(pm2.id),
            {'is_default': True},
            format='json',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        pm1.refresh_from_db()
        pm2.refresh_from_db()
        self.assertFalse(pm1.is_default)
        self.assertTrue(pm2.is_default)

    def test_patch_requires_manage_permission(self):
        pm = self._make_latam_pm()
        user_no_perm = make_user(self.tenant)
        self.client.force_authenticate(user=user_no_perm)

        resp = self.client.patch(
            self._pm_detail_url(pm.id),
            {'is_default': True},
            format='json',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    # ── DELETE ────────────────────────────────────────────────────────────────

    def test_delete_payment_method(self):
        # Create 2 PMs so deleting one is not blocked (not the last method)
        pm1 = self._make_latam_pm(external_type='paypal')
        self._make_latam_pm(external_type='yape')
        resp = self.client.delete(
            self._pm_detail_url(pm1.id),
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(PaymentMethod.objects.filter(id=pm1.id).exists())

    def test_delete_last_method_blocked_when_active_sub(self):
        pm = self._make_latam_pm()
        sub = Subscription.objects.get(tenant=self.tenant)
        sub.status = 'active'
        sub.save()

        resp = self.client.delete(
            self._pm_detail_url(pm.id),
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data['error']['code'], 'last_payment_method')

    def test_delete_last_method_allowed_when_no_sub(self):
        pm = self._make_latam_pm()
        Subscription.objects.filter(tenant=self.tenant).delete()

        resp = self.client.delete(
            self._pm_detail_url(pm.id),
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)

    def test_delete_requires_manage_permission(self):
        pm = self._make_latam_pm()
        user_no_perm = make_user(self.tenant)
        self.client.force_authenticate(user=user_no_perm)

        resp = self.client.delete(
            self._pm_detail_url(pm.id),
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
