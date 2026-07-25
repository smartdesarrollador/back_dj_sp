"""
Tests de la renovación: estado de vencimiento derivado y pago del plan actual.

Antes de esta fase renovar era imposible — `YapeUpgradeView` rechazaba pagar el plan
que ya tenías, así que un Enterprise no tenía forma de volver a pagar nunca. Ver
prd/features/renovacion-y-expiracion-de-planes.md y ADR-008, decisión 3.
"""
import uuid
from datetime import timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.promotions.models import Promotion, PromotionRedemption
from apps.subscriptions.models import Invoice, Plan, Subscription, YapePaymentProof
from apps.subscriptions.services import (
    RENEWAL_WINDOW_DAYS,
    activate_yape_proof,
    get_renewal_state,
    is_renewable,
)
from apps.tenants.models import Tenant
from core.tests.helpers import png_bytes

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']

UPGRADE_URL = '/api/v1/admin/subscriptions/yape-upgrade/'


def make_tenant(plan: str = 'free'):
    slug = f'tenant-{uuid.uuid4().hex[:8]}'
    return Tenant.objects.create(name=slug, slug=slug, subdomain=slug, plan=plan)


def make_user(tenant, is_superuser=True):
    from apps.auth_app.models import User
    return User.objects.create_user(
        email=f'user-{uuid.uuid4().hex[:8]}@example.com',
        name='Test User', password='testpass123',
        tenant=tenant, is_superuser=is_superuser, is_staff=is_superuser,
    )


def setup_subscription(plan='professional', days_left=None, **extra) -> Subscription:
    """Tenant + Subscription en el estado buscado. El signal ya creó la suscripción."""
    tenant = make_tenant(plan)
    sub = Subscription.objects.get(tenant=tenant)
    sub.plan = plan
    sub.status = extra.pop('status', 'active')
    if days_left is not None:
        sub.current_period_start = timezone.now() - timedelta(days=30 - days_left)
        sub.current_period_end = timezone.now() + timedelta(days=days_left)
    for field, value in extra.items():
        setattr(sub, field, value)
    sub.save()
    return sub


def add_paid_invoice(tenant, period_end=None):
    return Invoice.objects.create(
        tenant=tenant,
        stripe_invoice_id=f'inv_{uuid.uuid4().hex[:8]}',
        amount_cents=7900,
        status='paid',
        period_start=(period_end or timezone.now()) - timedelta(days=30),
        period_end=period_end or timezone.now(),
        invoice_date=timezone.now(),
    )


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS)
class TestRenewalState(TestCase):
    def test_active_far_from_expiry(self):
        sub = setup_subscription(days_left=25)
        self.assertEqual(get_renewal_state(sub), 'active')
        self.assertFalse(is_renewable(sub))

    def test_expiring_soon_inside_window(self):
        sub = setup_subscription(days_left=RENEWAL_WINDOW_DAYS - 5)
        self.assertEqual(get_renewal_state(sub), 'expiring_soon')
        self.assertTrue(is_renewable(sub))

    def test_expiring_soon_at_window_edge(self):
        sub = setup_subscription(days_left=RENEWAL_WINDOW_DAYS)
        self.assertEqual(get_renewal_state(sub), 'expiring_soon')

    def test_grace_when_past_due(self):
        sub = setup_subscription(
            days_left=-2, status='past_due',
            grace_until=timezone.now() + timedelta(days=5),
        )
        self.assertEqual(get_renewal_state(sub), 'grace')
        self.assertTrue(is_renewable(sub))

    def test_expired_when_free_after_having_paid(self):
        sub = setup_subscription(plan='free', days_left=-10)
        add_paid_invoice(sub.tenant)
        self.assertEqual(get_renewal_state(sub), 'expired')
        self.assertFalse(is_renewable(sub))  # se recontrata por el flujo de upgrade

    def test_free_that_never_paid_is_active(self):
        sub = setup_subscription(plan='free')
        self.assertEqual(get_renewal_state(sub), 'active')
        self.assertFalse(is_renewable(sub))

    def test_expired_trial_is_not_reported_as_expired_plan(self):
        """
        Un trial Professional vencido deja plan='free' con current_period_end en el
        pasado. Sin factura pagada no es un plan vencido: nunca pagó nada.
        """
        sub = setup_subscription(plan='free', days_left=-3)
        sub.tenant.professional_trial_used = True
        sub.tenant.save(update_fields=['professional_trial_used'])
        self.assertEqual(get_renewal_state(sub), 'active')

    def test_paid_plan_without_period_is_active(self):
        sub = setup_subscription(plan='professional', current_period_end=None)
        self.assertEqual(get_renewal_state(sub), 'active')
        self.assertFalse(is_renewable(sub))


class SubmitMixin:
    """
    Helpers del submit. Mixin y no clase base de test: heredar de una `APITestCase`
    reejecutaría sus tests en cada subclase.
    """

    def setUp(self):
        for plan_id, monthly, annual in [
            ('starter', 19, 200), ('professional', 79, 854), ('enterprise', 199, 2149),
        ]:
            Plan.objects.get_or_create(
                id=plan_id,
                defaults={
                    'display_name': plan_id.capitalize(),
                    'price_monthly': monthly, 'price_annual': annual,
                },
            )

    def _auth(self, sub):
        self.client.force_authenticate(user=make_user(sub.tenant))
        return {'HTTP_X_TENANT_SLUG': sub.tenant.slug}

    def _submit(self, sub, plan, amount='79', **extra):
        data = {
            'plan': plan, 'amount': amount,
            'screenshot': SimpleUploadedFile(
                'proof.png', png_bytes(), content_type='image/png'
            ),
            **extra,
        }
        return self.client.post(
            UPGRADE_URL, data, format='multipart', **self._auth(sub)
        )


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS)
class TestRenewalSubmit(SubmitMixin, APITestCase):
    def test_renew_inside_window(self):
        sub = setup_subscription('professional', days_left=10)

        resp = self._submit(sub, 'professional')

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(resp.data['is_renewal'])
        proof = YapePaymentProof.objects.get(id=resp.data['proof_id'])
        self.assertEqual(proof.plan, 'professional')

    def test_renew_during_grace(self):
        sub = setup_subscription(
            'professional', days_left=-3, status='past_due',
            grace_until=timezone.now() + timedelta(days=4),
        )

        resp = self._submit(sub, 'professional')

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(resp.data['is_renewal'])

    def test_renew_outside_window_rejected(self):
        sub = setup_subscription('professional', days_left=25)

        resp = self._submit(sub, 'professional')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('no está próximo a vencer', resp.data['detail'])
        self.assertFalse(YapePaymentProof.objects.exists())

    def test_enterprise_can_renew(self):
        """El plan máximo era el caso sin salida: no había plan superior que pagar."""
        sub = setup_subscription('enterprise', days_left=5)

        resp = self._submit(sub, 'enterprise', amount='199')

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(resp.data['is_renewal'])

    def test_upgrade_still_works(self):
        sub = setup_subscription('starter', days_left=25)

        resp = self._submit(sub, 'professional')

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertFalse(resp.data['is_renewal'])

    def test_upgrade_ignores_renewal_window(self):
        """Mejorar de plan no depende de estar cerca del vencimiento."""
        sub = setup_subscription('starter', days_left=29)

        resp = self._submit(sub, 'enterprise', amount='199')

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

    def test_downgrade_by_paying_rejected(self):
        sub = setup_subscription('professional', days_left=5)

        resp = self._submit(sub, 'starter', amount='19')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('no es un upgrade', resp.data['detail'])


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS)
class TestRenewalBillingCycle(SubmitMixin, APITestCase):
    def test_annual_cycle_is_persisted_and_priced(self):
        sub = setup_subscription('professional', days_left=5)

        resp = self._submit(sub, 'professional', billing_cycle='annual')

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['billing_cycle'], 'annual')
        proof = YapePaymentProof.objects.get(id=resp.data['proof_id'])
        self.assertEqual(proof.billing_cycle, 'annual')
        self.assertEqual(proof.amount, Decimal('854.00'))

    def test_cycle_defaults_to_monthly(self):
        sub = setup_subscription('professional', days_left=5)

        resp = self._submit(sub, 'professional')

        proof = YapePaymentProof.objects.get(id=resp.data['proof_id'])
        self.assertEqual(proof.billing_cycle, 'monthly')
        self.assertEqual(proof.amount, Decimal('79.00'))

    def test_invalid_cycle_rejected(self):
        sub = setup_subscription('professional', days_left=5)

        resp = self._submit(sub, 'professional', billing_cycle='yearly')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(YapePaymentProof.objects.exists())

    def test_annual_amount_with_promo_is_server_side(self):
        sub = setup_subscription('professional', days_left=5)
        Promotion.objects.create(
            code='ANUAL20', name='Anual 20', type='percentage', value=Decimal('20'),
            applicable_plans=['professional'], new_customers_only=False,
            starts_at=timezone.now() - timedelta(days=1),
            expires_at=timezone.now() + timedelta(days=30),
        )

        resp = self._submit(
            sub, 'professional', amount='1',  # monto falso del cliente
            billing_cycle='annual', promo_code='ANUAL20',
        )

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        proof = YapePaymentProof.objects.get(id=resp.data['proof_id'])
        self.assertEqual(proof.amount, Decimal('683.20'))  # 854 - 20%
        redemption = PromotionRedemption.objects.get(yape_proof=proof)
        self.assertEqual(redemption.original_amount, Decimal('854.00'))


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS)
class TestPendingProofIdempotency(SubmitMixin, APITestCase):
    def test_second_submit_conflicts(self):
        sub = setup_subscription('professional', days_left=5)
        first = self._submit(sub, 'professional')
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)

        second = self._submit(sub, 'professional')

        self.assertEqual(second.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(second.data['proof_id'], first.data['proof_id'])
        self.assertEqual(YapePaymentProof.objects.count(), 1)

    def test_pending_proof_blocks_a_different_plan_too(self):
        """Dos pendientes podrían aprobarse ambos y cobrar dos veces."""
        sub = setup_subscription('starter', days_left=25)
        self._submit(sub, 'professional')

        second = self._submit(sub, 'enterprise', amount='199')

        self.assertEqual(second.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(YapePaymentProof.objects.count(), 1)

    def test_submit_allowed_again_after_approval(self):
        sub = setup_subscription('professional', days_left=5)
        first = self._submit(sub, 'professional')
        activate_yape_proof(YapePaymentProof.objects.get(id=first.data['proof_id']))

        sub.refresh_from_db()
        sub.current_period_end = timezone.now() + timedelta(days=3)
        sub.save(update_fields=['current_period_end'])
        second = self._submit(sub, 'professional')

        self.assertEqual(second.status_code, status.HTTP_201_CREATED)
        self.assertEqual(YapePaymentProof.objects.count(), 2)

    def test_rejected_proof_does_not_block(self):
        sub = setup_subscription('professional', days_left=5)
        first = self._submit(sub, 'professional')
        proof = YapePaymentProof.objects.get(id=first.data['proof_id'])
        proof.status = 'rejected'
        proof.save(update_fields=['status'])

        second = self._submit(sub, 'professional')

        self.assertEqual(second.status_code, status.HTTP_201_CREATED)
