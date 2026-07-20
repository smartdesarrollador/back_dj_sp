"""
Tests E2E del flujo de canje: registro con plan pago → upload del comprobante
Yape con cupón → aprobación/rechazo → ciclo de vida de la redemption.

Covers: monto SIEMPRE server-side (el amount del cliente se ignora), redemption
pending al subir, token peek/consume (un cupón rechazado en submit no quema el
token), confirmación al aprobar (current_uses+1 con lock, carrera depleted
aprueba igual), liberación en ambos caminos de rechazo (admin PATCH y one-click
público), activación directa por cupón 100% y payload del webhook n8n.
"""
import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.promotions.models import Promotion, PromotionRedemption
from apps.subscriptions.models import Invoice, Plan, Subscription, YapePaymentProof
from apps.subscriptions.services import activate_yape_proof
from apps.tenants.models import Tenant

User = get_user_model()

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

REGISTER_URL = '/api/v1/auth/register'
PROOF_URL = '/api/v1/auth/yape-payment-proof'
ACTIVATE_FREE_URL = '/api/v1/auth/yape-activate-free'


def _register_payload(**kwargs):
    uid = uuid.uuid4().hex[:6]
    return {
        'name': 'Test User',
        'email': f'u-{uid}@test.com',
        'password': 'SecurePass1!',
        'organization_name': f'Org {uid}',
        **kwargs,
    }


def _create_promotion(**overrides) -> Promotion:
    now = timezone.now()
    defaults = {
        'code': 'VERANO20',
        'name': 'Promo Verano',
        'type': 'percentage',
        'value': Decimal('20'),
        'applicable_plans': ['starter', 'professional'],
        'starts_at': now - timedelta(days=1),
        'expires_at': now + timedelta(days=30),
    }
    defaults.update(overrides)
    return Promotion.objects.create(**defaults)


def _screenshot():
    return SimpleUploadedFile('proof.png', b'\x89PNG fake image bytes', content_type='image/png')


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class YapeProofWithPromoTests(APITestCase):
    """Upload del comprobante: recálculo server-side y creación de la redemption."""

    def setUp(self):
        cache.clear()
        Plan.objects.create(id='starter', display_name='Starter', price_monthly=19)

    def _register_paid(self, plan='starter'):
        with patch('apps.auth_app.views.send_mail', return_value=1):
            response = self.client.post(REGISTER_URL, _register_payload(plan=plan), format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data['requires_payment'])
        return response.data['payment_upload_token']

    def _upload(self, token, plan='starter', promo_code=None, amount='1.00'):
        data = {
            'payment_upload_token': token,
            'screenshot': _screenshot(),
            'plan': plan,
            'amount': amount,  # monto falso del cliente — debe ignorarse siempre
        }
        if promo_code is not None:
            data['promo_code'] = promo_code
        with patch('apps.subscriptions.tasks.notify_yape_payment.delay') as mock_delay:
            response = self.client.post(PROOF_URL, data, format='multipart')
        return response, mock_delay

    def test_amount_is_server_side_without_promo(self):
        token = self._register_paid()
        response, mock_delay = self._upload(token, amount='0.01')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        proof = YapePaymentProof.objects.get(pk=response.data['proof_id'])
        self.assertEqual(proof.amount, Decimal('19.00'))  # precio Plan, no el 0.01 del cliente
        self.assertFalse(PromotionRedemption.objects.exists())
        mock_delay.assert_called_once_with(str(proof.id))

    def test_amount_falls_back_to_catalog_without_plan_row(self):
        Plan.objects.all().delete()
        token = self._register_paid()
        response, _ = self._upload(token)
        proof = YapePaymentProof.objects.get(pk=response.data['proof_id'])
        self.assertEqual(proof.amount, Decimal('29.00'))  # PLAN_CATALOG starter

    def test_upload_with_promo_creates_pending_redemption(self):
        promo = _create_promotion()
        token = self._register_paid()
        response, _ = self._upload(token, promo_code='verano20')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        proof = YapePaymentProof.objects.get(pk=response.data['proof_id'])
        self.assertEqual(proof.amount, Decimal('15.20'))  # 19 − 20%

        redemption = proof.redemption
        self.assertEqual(redemption.status, 'pending')
        self.assertEqual(redemption.promotion, promo)
        self.assertEqual(redemption.original_amount, Decimal('19.00'))
        self.assertEqual(redemption.discount_amount, Decimal('3.80'))
        self.assertEqual(redemption.final_amount, Decimal('15.20'))
        promo.refresh_from_db()
        self.assertEqual(promo.current_uses, 0)  # se cuenta recién al aprobar

    def test_invalid_promo_does_not_burn_token(self):
        _create_promotion(max_uses=1, current_uses=1)  # agotada
        token = self._register_paid()

        response, mock_delay = self._upload(token, promo_code='VERANO20')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data.get('promo_reason'), 'depleted')
        self.assertFalse(YapePaymentProof.objects.exists())
        mock_delay.assert_not_called()

        # El token sigue vivo: reintento sin cupón funciona
        retry, _ = self._upload(token)
        self.assertEqual(retry.status_code, status.HTTP_201_CREATED)

    def test_token_consumed_after_success(self):
        token = self._register_paid()
        first, _ = self._upload(token)
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        second, _ = self._upload(token)
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)

    def test_per_customer_limit_counts_pending(self):
        _create_promotion(max_uses_per_customer=1)
        token = self._register_paid()
        first, _ = self._upload(token, promo_code='VERANO20')
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)

        # Mismo tenant intenta canjear otra vez (nuevo token simulado)
        from apps.auth_app.tokens import create_payment_upload_token
        tenant = YapePaymentProof.objects.first().subscription.tenant
        token2 = create_payment_upload_token(str(tenant.id))
        second, _ = self._upload(token2, promo_code='VERANO20')
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class RedemptionLifecycleTests(APITestCase):
    """Aprobación y rechazo del comprobante — confirmar / liberar el canje."""

    def setUp(self):
        cache.clear()
        Plan.objects.create(id='starter', display_name='Starter', price_monthly=19)
        self.promo = _create_promotion()
        self.tenant = Tenant.objects.create(
            name='Acme', slug='acme', subdomain='acme', plan='free',
        )
        self.user = User.objects.create_user(
            email='owner@acme.com', name='Owner', password='pass123', tenant=self.tenant,
        )
        self.subscription = Subscription.objects.get(tenant=self.tenant)
        self.subscription.plan = 'starter'
        self.subscription.status = 'pending_payment'
        self.subscription.save(update_fields=['plan', 'status', 'updated_at'])

        self.proof = YapePaymentProof.objects.create(
            subscription=self.subscription,
            screenshot=_screenshot(),
            plan='starter',
            amount=Decimal('15.20'),
            admin_token=uuid.uuid4().hex,
        )
        self.redemption = PromotionRedemption.objects.create(
            promotion=self.promo,
            tenant=self.tenant,
            yape_proof=self.proof,
            plan='starter',
            original_amount=Decimal('19.00'),
            discount_amount=Decimal('3.80'),
            final_amount=Decimal('15.20'),
        )

    def test_approve_confirms_redemption_and_counts_use(self):
        invoice = activate_yape_proof(self.proof)

        self.redemption.refresh_from_db()
        self.promo.refresh_from_db()
        self.assertEqual(self.redemption.status, 'confirmed')
        self.assertIsNotNone(self.redemption.confirmed_at)
        self.assertEqual(self.promo.current_uses, 1)
        self.assertIsNotNone(self.promo.last_used_at)
        self.assertEqual(invoice.amount_cents, 1520)  # Invoice con el monto descontado

        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.plan, 'starter')

    def test_approve_over_depleted_still_confirms(self):
        self.promo.max_uses = 1
        self.promo.current_uses = 1
        self.promo.save(update_fields=['max_uses', 'current_uses'])

        activate_yape_proof(self.proof)

        self.redemption.refresh_from_db()
        self.promo.refresh_from_db()
        self.assertEqual(self.redemption.status, 'confirmed')
        self.assertEqual(self.promo.current_uses, 2)  # el pago ya hecho se respeta

    def test_admin_reject_releases_redemption(self):
        staff = User.objects.create_user(
            email='staff@acme.com', name='Staff', password='pass123',
            tenant=self.tenant, is_staff=True,
        )
        self.client.force_authenticate(user=staff)
        response = self.client.patch(
            f'/api/v1/admin/yape/proofs/{self.proof.id}/review/',
            {'status': 'rejected'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.redemption.refresh_from_db()
        self.promo.refresh_from_db()
        self.assertEqual(self.redemption.status, 'released')
        self.assertEqual(self.promo.current_uses, 0)

    def test_public_one_click_reject_releases_redemption(self):
        response = self.client.post(
            f'/api/v1/public/yape-payment/reject/{self.proof.admin_token}/',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.redemption.refresh_from_db()
        self.assertEqual(self.redemption.status, 'released')
        self.proof.refresh_from_db()
        self.assertEqual(self.proof.status, 'rejected')

    def test_notify_payload_includes_promo_breakdown(self):
        from apps.subscriptions.tasks import notify_yape_payment

        with override_settings(N8N_YAPE_PAYMENT_WEBHOOK_URL='http://n8n.test/webhook'):
            with patch('apps.subscriptions.tasks.requests.post') as mock_post:
                mock_post.return_value.status_code = 200
                notify_yape_payment(str(self.proof.id))

        payload = mock_post.call_args.kwargs['json']
        self.assertEqual(payload['promo'], {
            'code': 'VERANO20',
            'original_amount': '19.00',
            'discount_amount': '3.80',
            'final_amount': '15.20',
        })
        self.assertEqual(payload['amount'], '15.20')
        # El tipo de cambio real (YapeConfig) viaja en el payload para que n8n
        # calcule el S/ con el mismo rate que vio el cliente
        self.assertEqual(payload['exchange_rate'], '3.75')

    def test_admin_proofs_list_includes_promo_breakdown(self):
        staff = User.objects.create_user(
            email='staff-list@acme.com', name='Staff', password='pass123',
            tenant=self.tenant, is_staff=True,
        )
        # Segundo proof sin cupón para verificar promo: None
        YapePaymentProof.objects.create(
            subscription=self.subscription,
            screenshot=_screenshot(),
            plan='starter',
            amount=Decimal('19.00'),
            admin_token=uuid.uuid4().hex,
        )
        self.client.force_authenticate(user=staff)
        response = self.client.get('/api/v1/admin/yape/proofs/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        by_id = {p['id']: p for p in response.data['proofs']}
        with_promo = by_id[str(self.proof.id)]
        self.assertEqual(with_promo['promo'], {
            'code': 'VERANO20',
            'original_amount': '19.00',
            'discount_amount': '3.80',
            'final_amount': '15.20',
        })
        without_promo = next(p for pid, p in by_id.items() if pid != str(self.proof.id))
        self.assertIsNone(without_promo['promo'])

    def test_notify_payload_promo_is_none_without_redemption(self):
        from apps.subscriptions.tasks import notify_yape_payment

        self.redemption.delete()
        with override_settings(N8N_YAPE_PAYMENT_WEBHOOK_URL='http://n8n.test/webhook'):
            with patch('apps.subscriptions.tasks.requests.post') as mock_post:
                mock_post.return_value.status_code = 200
                notify_yape_payment(str(self.proof.id))

        self.assertIsNone(mock_post.call_args.kwargs['json']['promo'])


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class ActivateFreeTests(APITestCase):
    """Activación directa por cupón 100% — sin comprobante."""

    def setUp(self):
        cache.clear()
        Plan.objects.create(id='starter', display_name='Starter', price_monthly=19)

    def _register_paid(self, plan='starter'):
        with patch('apps.auth_app.views.send_mail', return_value=1):
            response = self.client.post(REGISTER_URL, _register_payload(plan=plan), format='json')
        return response.data['payment_upload_token']

    def _activate(self, token, plan='starter', promo_code='GRATIS100'):
        return self.client.post(ACTIVATE_FREE_URL, {
            'payment_upload_token': token,
            'plan': plan,
            'promo_code': promo_code,
        }, format='json')

    def test_hundred_percent_activates_directly(self):
        promo = _create_promotion(code='GRATIS100', value=Decimal('100'))
        token = self._register_paid()

        response = self._activate(token)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['activated'])

        tenant = Tenant.objects.exclude(slug='').latest('created_at')
        self.assertEqual(tenant.plan, 'starter')
        subscription = Subscription.objects.get(tenant=tenant)
        self.assertEqual(subscription.status, 'active')

        redemption = PromotionRedemption.objects.get(tenant=tenant)
        self.assertEqual(redemption.status, 'confirmed')
        self.assertIsNone(redemption.yape_proof)
        promo.refresh_from_db()
        self.assertEqual(promo.current_uses, 1)

        invoice = Invoice.objects.get(tenant=tenant)
        self.assertEqual(invoice.amount_cents, 0)
        self.assertEqual(invoice.status, 'paid')

        # Token consumido: segundo intento falla
        second = self._activate(token)
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)

    def test_non_free_coupon_is_rejected(self):
        _create_promotion(code='GRATIS100', value=Decimal('50'))
        token = self._register_paid()
        response = self._activate(token)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data.get('promo_reason'), 'not_free')
        self.assertFalse(PromotionRedemption.objects.exists())
        # El token no se quemó
        retry = self._activate(token)
        self.assertEqual(retry.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNotIn('token', str(retry.data))

    def test_missing_promo_code_is_400(self):
        token = self._register_paid()
        response = self.client.post(ACTIVATE_FREE_URL, {
            'payment_upload_token': token, 'plan': 'starter',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
