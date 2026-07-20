"""Tests for subscription billing views. All Stripe API calls are mocked."""
import base64
import json
import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import stripe as stripe_lib
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.promotions.models import Promotion, PromotionRedemption
from apps.subscriptions.models import Invoice, PaymentMethod, Plan, Subscription, YapePaymentProof
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
            '/api/v1/admin/subscriptions/current/',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_current_subscription_creates_if_not_exists(self):
        # Remove auto-created subscription from signal
        Subscription.objects.filter(tenant=self.tenant).delete()

        resp = self.client.get(
            '/api/v1/admin/subscriptions/current/',
            **slug_header(self.tenant.slug),
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('subscription', resp.data)
        self.assertTrue(Subscription.objects.filter(tenant=self.tenant).exists())

    def test_current_subscription_returns_existing(self):
        self.tenant.plan = 'starter'
        self.tenant.save(update_fields=['plan'])
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
            '/api/v1/admin/subscriptions/current/',
            **slug_header(self.tenant.slug),
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['subscription']['plan'], 'starter')

    def test_current_subscription_reflects_tenant_plan_when_desynced(self):
        # Bug real reportado: Subscription.plan puede quedar desincronizado de Tenant.plan
        # (p. ej. sembrado por el signal antes de este fix, o editado directo). La respuesta
        # debe reflejar siempre Tenant.plan — la misma fuente que usa el topbar del Hub.
        self.tenant.plan = 'professional'
        self.tenant.save(update_fields=['plan'])
        sub, _ = Subscription.objects.get_or_create(tenant=self.tenant)
        sub.plan = 'free'
        sub.save()

        resp = self.client.get(
            '/api/v1/admin/subscriptions/current/',
            **slug_header(self.tenant.slug),
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['subscription']['plan'], 'professional')

    def test_current_subscription_includes_usage(self):
        resp = self.client.get(
            '/api/v1/admin/subscriptions/current/',
            **slug_header(self.tenant.slug),
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('usage', resp.data['subscription'])
        usage = resp.data['subscription']['usage']
        self.assertIn('users', usage)
        self.assertIn('storage', usage)
        self.assertIn('services', usage)


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
            '/api/v1/admin/subscriptions/upgrade/',
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
            '/api/v1/admin/subscriptions/upgrade/',
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
            '/api/v1/admin/subscriptions/upgrade/',
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
            '/api/v1/admin/subscriptions/upgrade/',
            {'new_plan': 'starter', 'billing_cycle': 'monthly'},
            format='json',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_upgrade_invalid_plan_rejected(self):
        resp = self.client.post(
            '/api/v1/admin/subscriptions/upgrade/',
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
            '/api/v1/admin/subscriptions/cancel/',
            format='json',
            **slug_header(self.tenant.slug),
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.sub.refresh_from_db()
        self.assertTrue(self.sub.cancel_at_period_end)

    def test_cancel_requires_auth(self):
        client = APIClient()
        resp = client.post(
            '/api/v1/admin/subscriptions/cancel/',
            format='json',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_cancel_requires_permission(self):
        user_no_perm = make_user(self.tenant)
        self.client.force_authenticate(user=user_no_perm)

        resp = self.client.post(
            '/api/v1/admin/subscriptions/cancel/',
            format='json',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_cancel_no_stripe_id_updates_flag_only(self):
        """If no stripe_subscription_id, just sets cancel_at_period_end."""
        self.sub.stripe_subscription_id = ''
        self.sub.save()

        resp = self.client.post(
            '/api/v1/admin/subscriptions/cancel/',
            format='json',
            **slug_header(self.tenant.slug),
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.sub.refresh_from_db()
        self.assertTrue(self.sub.cancel_at_period_end)


# ─── YapeUpgradeView ──────────────────────────────────────────────────────────

@override_settings(PASSWORD_HASHERS=_FAST_HASHERS)
class TestYapeUpgradeView(APITestCase):
    def setUp(self):
        self.tenant = make_tenant(plan='free')
        self.user = make_user(self.tenant, is_superuser=True)
        self.client.force_authenticate(user=self.user)
        Plan.objects.get_or_create(
            id='professional', defaults={'display_name': 'Professional', 'price_monthly': 79},
        )

    def _screenshot(self):
        return SimpleUploadedFile('proof.png', b'\x89PNG fake', content_type='image/png')

    def _create_promotion(self, **overrides):
        now = timezone.now()
        defaults = {
            'code': 'UPGRADE20',
            'name': 'Upgrade Promo',
            'type': 'percentage',
            'value': Decimal('20'),
            'applicable_plans': ['professional'],
            'starts_at': now - timedelta(days=1),
            'expires_at': now + timedelta(days=30),
        }
        defaults.update(overrides)
        return Promotion.objects.create(**defaults)

    def _upgrade(self, plan='professional', amount='79', promo_code=None):
        data = {'plan': plan, 'screenshot': self._screenshot(), 'amount': amount}
        if promo_code is not None:
            data['promo_code'] = promo_code
        return self.client.post(
            '/api/v1/admin/subscriptions/yape-upgrade/',
            data, format='multipart', **slug_header(self.tenant.slug),
        )

    def test_yape_upgrade_success(self):
        resp = self._upgrade()

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertIn('proof_id', resp.data)
        proof = YapePaymentProof.objects.get(id=resp.data['proof_id'])
        self.assertEqual(proof.plan, 'professional')
        self.assertEqual(proof.subscription.tenant, self.tenant)

    def test_yape_upgrade_requires_auth(self):
        client = APIClient()
        resp = client.post(
            '/api/v1/admin/subscriptions/yape-upgrade/',
            {'plan': 'professional', 'screenshot': self._screenshot(), 'amount': '79'},
            format='multipart',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_yape_upgrade_same_or_lower_plan_rejected(self):
        resp = self._upgrade(plan='free', amount='0')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_yape_upgrade_amount_is_server_side(self):
        resp = self._upgrade(amount='0.01')  # monto falso del cliente — debe ignorarse
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        proof = YapePaymentProof.objects.get(id=resp.data['proof_id'])
        self.assertEqual(str(proof.amount), '79.00')

    def test_yape_upgrade_with_promo_creates_pending_redemption(self):
        self._create_promotion()
        resp = self._upgrade(amount='999', promo_code='upgrade20')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

        proof = YapePaymentProof.objects.get(id=resp.data['proof_id'])
        self.assertEqual(str(proof.amount), '63.20')  # 79 − 20%

        redemption = proof.redemption
        self.assertEqual(redemption.status, 'pending')
        self.assertEqual(redemption.tenant, self.tenant)
        self.assertEqual(str(redemption.original_amount), '79.00')
        self.assertEqual(str(redemption.discount_amount), '15.80')
        self.assertEqual(str(redemption.final_amount), '63.20')

    def test_yape_upgrade_invalid_promo_rejected(self):
        self._create_promotion(max_uses=1, current_uses=1)  # agotada
        resp = self._upgrade(promo_code='UPGRADE20')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('promo_reason'), 'depleted')
        self.assertFalse(YapePaymentProof.objects.exists())

    def test_yape_upgrade_new_customers_only_promo_rejected_for_existing_tenant(self):
        Invoice.objects.create(
            tenant=self.tenant, stripe_invoice_id='inv_existing', amount_cents=2900,
            currency='usd', status='paid',
        )
        self._create_promotion(new_customers_only=True)
        resp = self._upgrade(promo_code='UPGRADE20')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('promo_reason'), 'new_customers_only')
        self.assertFalse(YapePaymentProof.objects.exists())

    def test_yape_upgrade_without_promo_unchanged(self):
        resp = self._upgrade()
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        proof = YapePaymentProof.objects.get(id=resp.data['proof_id'])
        self.assertEqual(str(proof.amount), '79.00')
        self.assertFalse(PromotionRedemption.objects.filter(yape_proof=proof).exists())


# ─── StartTrialView ───────────────────────────────────────────────────────────

@override_settings(PASSWORD_HASHERS=_FAST_HASHERS)
class TestStartTrialView(APITestCase):
    def setUp(self):
        self.tenant = make_tenant(plan='free')
        self.user = make_user(self.tenant, is_superuser=True)
        self.client.force_authenticate(user=self.user)

    def test_start_trial_success(self):
        resp = self.client.post(
            '/api/v1/admin/subscriptions/trial/',
            format='json',
            **slug_header(self.tenant.slug),
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.plan, 'professional')
        self.assertTrue(self.tenant.professional_trial_used)

    def test_start_trial_requires_auth(self):
        client = APIClient()
        resp = client.post(
            '/api/v1/admin/subscriptions/trial/',
            format='json',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_start_trial_already_used_rejected(self):
        self.tenant.professional_trial_used = True
        self.tenant.save(update_fields=['professional_trial_used'])

        resp = self.client.post(
            '/api/v1/admin/subscriptions/trial/',
            format='json',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


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
            '/api/v1/admin/billing/invoices/',
            **slug_header(self.tenant.slug),
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data['invoices']), 1)
        self.assertEqual(resp.data['invoices'][0]['amount_cents'], 1000)

    def test_invoice_list_requires_auth(self):
        client = APIClient()
        resp = client.get(
            '/api/v1/admin/billing/invoices/',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_invoice_list_empty(self):
        resp = self.client.get(
            '/api/v1/admin/billing/invoices/',
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
            '/api/v1/admin/billing/webhooks/',
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
_PM_LIST_URL = f'{_PM_URL}/'


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
        resp = self.client.get(_PM_LIST_URL, **slug_header(self.tenant.slug))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data['payment_methods']), 1)

    def test_list_requires_auth(self):
        client = APIClient()
        resp = client.get(_PM_LIST_URL, **slug_header(self.tenant.slug))
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_list_tenant_isolation(self):
        self._make_latam_pm(tenant=self.other_tenant)
        resp = self.client.get(_PM_LIST_URL, **slug_header(self.tenant.slug))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['payment_methods'], [])

    # ── Create (LATAM) ────────────────────────────────────────────────────────

    def test_create_latam_paypal(self):
        resp = self.client.post(
            _PM_LIST_URL,
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
            _PM_LIST_URL,
            {'external_type': 'yape', 'external_phone': '+51999888777', 'is_default': False},
            format='json',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['payment_method']['external_type'], 'yape')
        self.assertEqual(resp.data['payment_method']['external_phone'], '+51999888777')

    def test_create_encrypts_account_id(self):
        resp = self.client.post(
            _PM_LIST_URL,
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
            _PM_LIST_URL,
            {'is_default': True},
            format='json',
            **slug_header(self.tenant.slug),
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_both_methods_rejected(self):
        resp = self.client.post(
            _PM_LIST_URL,
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
            _PM_LIST_URL,
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
