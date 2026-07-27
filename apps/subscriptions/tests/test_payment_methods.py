"""
Tests del método de pago como dato: catálogo de métodos, sus guardarraíles y lo que
cambia en el comprobante según el método por el que se pagó.

Los canarios de no-regresión de la fachada heredada de Yape vivían aquí y se
retiraron con ella: sus endpoints ya no existen.
"""
import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.subscriptions.models import (
    CurrencyConfig,
    PaymentMethodConfig,
    PaymentProof,
    Plan,
    Subscription,
)
from apps.subscriptions.payment_methods import (
    accepts_proofs,
    charges_in_pen,
    get_enabled_methods,
)
from apps.tenants.models import Tenant
from core.tests.helpers import png_bytes

User = get_user_model()

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

PUBLIC_METHODS_URL = '/api/v1/public/payment-methods/'
ADMIN_METHODS_URL = '/api/v1/admin/payments/methods/'


def _create_tenant(slug, plan='professional'):
    return Tenant.objects.create(name=slug.capitalize(), slug=slug, subdomain=slug, plan=plan)


def _create_superuser(tenant, email):
    return User.objects.create_user(
        email=email, name='Owner', password='pass123', tenant=tenant,
        is_superuser=True, is_staff=True,
    )


def _set_method(method, **fields):
    config, _ = PaymentMethodConfig.objects.get_or_create(
        method=method, defaults={'display_name': method.capitalize()}
    )
    for key, value in fields.items():
        setattr(config, key, value)
    config.save()
    return config


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestPublicPaymentMethods(APITestCase):
    def setUp(self):
        cache.clear()
        _set_method('yape', is_enabled=True, phone='999888777', display_name='Yape', sort_order=10)
        _set_method('paypal', is_enabled=False, display_name='PayPal', sort_order=20)

    def test_lists_only_what_can_actually_be_paid(self):
        response = self.client.get(PUBLIC_METHODS_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([m['method'] for m in response.json()['methods']], ['yape'])

    def test_requires_no_auth(self):
        self.assertEqual(self.client.get(PUBLIC_METHODS_URL).status_code, status.HTTP_200_OK)

    def test_hides_an_enabled_method_left_without_its_data(self):
        # Un método publicado sin destino de pago lleva al cliente hasta el final del
        # flujo para dejarlo sin saber a dónde pagar.
        _set_method('yape', is_enabled=True, phone='')

        response = self.client.get(PUBLIC_METHODS_URL)

        self.assertEqual(response.json()['methods'], [])

    def test_shows_paypal_once_configured_and_enabled(self):
        _set_method('paypal', is_enabled=True, checkout_url='https://paypal.me/x')

        methods = self.client.get(PUBLIC_METHODS_URL).json()['methods']

        self.assertEqual([m['method'] for m in methods], ['yape', 'paypal'])
        self.assertEqual(methods[1]['checkout_url'], 'https://paypal.me/x')

    def test_contract_is_stable_across_methods(self):
        # Los campos que no aplican van vacíos, no ausentes: así el Hub no necesita
        # ramificar por método para leer la respuesta.
        _set_method('paypal', is_enabled=True, account_email='pagos@ejemplo.com')

        for method in self.client.get(PUBLIC_METHODS_URL).json()['methods']:
            self.assertEqual(
                set(method.keys()),
                {'method', 'display_name', 'charge_currency', 'requires_reference',
                 'holder_name', 'phone', 'checkout_url', 'account_email',
                 'instructions_note'},
            )

    def test_tells_the_hub_in_which_currency_each_method_charges(self):
        # El Hub decide con esto si pide soles o dólares. Va en la respuesta y no
        # duplicado en TypeScript: un método nuevo se añade en el backend y el frontend
        # lo trata bien sin desplegarse.
        _set_method('paypal', is_enabled=True, checkout_url='https://paypal.me/x')

        by_method = {m['method']: m for m in self.client.get(PUBLIC_METHODS_URL).json()['methods']}

        self.assertEqual(by_method['yape']['charge_currency'], 'PEN')
        self.assertEqual(by_method['paypal']['charge_currency'], 'USD')
        self.assertFalse(by_method['yape']['requires_reference'])
        self.assertTrue(by_method['paypal']['requires_reference'])


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestAdminPaymentMethods(APITestCase):
    def setUp(self):
        cache.clear()
        _set_method('yape', is_enabled=True, phone='999888777', display_name='Yape')
        _set_method('paypal', is_enabled=False, display_name='PayPal')
        self.tenant = _create_tenant('methods-corp')
        self.owner = _create_superuser(self.tenant, 'owner@methods-corp.com')
        self.client.force_authenticate(user=self.owner)
        self.headers = {'HTTP_X_TENANT_SLUG': 'methods-corp'}

    def test_lists_disabled_methods_too(self):
        # Al contrario que el público: el admin necesita ver lo que aún no configuró.
        response = self.client.get(ADMIN_METHODS_URL, **self.headers)

        self.assertEqual(
            {m['method'] for m in response.json()['methods']}, {'yape', 'paypal'},
        )

    def test_reports_whether_each_method_is_configured(self):
        response = self.client.get(ADMIN_METHODS_URL, **self.headers)
        by_method = {m['method']: m for m in response.json()['methods']}

        self.assertTrue(by_method['yape']['is_configured'])
        self.assertFalse(by_method['paypal']['is_configured'])

    def test_cannot_enable_a_method_without_its_payment_destination(self):
        response = self.client.patch(
            f'{ADMIN_METHODS_URL}paypal/', {'is_enabled': True}, format='json', **self.headers
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        body = str(response.json())
        self.assertIn('sin saber a dónde pagar', body)
        self.assertNotIn('Validation error', body)
        self.assertFalse(PaymentMethodConfig.objects.get(method='paypal').is_enabled)

    def test_enabling_with_the_destination_in_the_same_request_works(self):
        response = self.client.patch(
            f'{ADMIN_METHODS_URL}paypal/',
            {'is_enabled': True, 'checkout_url': 'https://paypal.me/x'},
            format='json', **self.headers,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(PaymentMethodConfig.objects.get(method='paypal').is_enabled)

    def test_cannot_blank_the_destination_of_an_enabled_method(self):
        # La validación mira el estado RESULTANTE, no solo el payload: si no, bastaría
        # con habilitar en un PATCH y borrar el teléfono en el siguiente.
        response = self.client.patch(
            f'{ADMIN_METHODS_URL}yape/', {'phone': ''}, format='json', **self.headers
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unknown_method_returns_404(self):
        response = self.client.patch(
            f'{ADMIN_METHODS_URL}bitcoin/', {'is_enabled': True}, format='json', **self.headers
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_requires_staff(self):
        regular = User.objects.create_user(
            email='member@methods-corp.com', name='Member', password='pass123',
            tenant=self.tenant,
        )
        self.client.force_authenticate(user=regular)

        response = self.client.get(ADMIN_METHODS_URL, **self.headers)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestProofMethod(APITestCase):
    def setUp(self):
        cache.clear()
        _set_method('yape', is_enabled=True, phone='999888777')
        _set_method('paypal', is_enabled=False)

    def _make_proof(self, **extra):
        slug = f'proof-{uuid.uuid4().hex[:8]}'
        tenant = Tenant.objects.create(name=slug, slug=slug, subdomain=slug, plan='free')
        sub = Subscription.objects.get(tenant=tenant)
        return PaymentProof.objects.create(
            subscription=sub, screenshot='payment_proofs/x.png', plan='professional',
            billing_cycle='monthly', amount=Decimal('79.00'),
            admin_token=uuid.uuid4().hex, **extra,
        )

    def test_defaults_to_yape_for_backward_compatibility(self):
        # Lo que hace que la fase sea aditiva: un comprobante creado como siempre
        # queda clasificado sin migrar nada.
        self.assertEqual(self._make_proof().method, 'yape')

    def test_accepts_proofs_only_for_enabled_methods(self):
        # El guardarraíl que impide que un recibo de PayPal llegue a la verificación
        # automática, escrita para leer capturas de Yape.
        self.assertTrue(accepts_proofs('yape'))
        self.assertFalse(accepts_proofs('paypal'))
        self.assertFalse(accepts_proofs('bitcoin'))

    def test_accepting_a_proof_does_not_require_the_method_to_be_configured(self):
        # Ofrecer un método y aceptar un pago ya hecho son cosas distintas: si el admin
        # borra el teléfono, deja de ofrecerse, pero rechazar el comprobante de quien
        # ya transfirió sería castigarle por un cambio ajeno.
        _set_method('yape', is_enabled=True, phone='')

        self.assertEqual(get_enabled_methods(), [])
        self.assertTrue(accepts_proofs('yape'))

    def test_serializer_exposes_method_and_reference(self):
        from apps.subscriptions.payment_admin_views import _serialize_proof

        proof = self._make_proof(method='yape', transaction_reference='8XY123456')
        data = _serialize_proof(proof)

        self.assertEqual(data['method'], 'yape')
        self.assertEqual(data['transaction_reference'], '8XY123456')
        # El panel lo usa para no explicar un pago en dólares como si fuera un
        # comprobante viejo sin tasa: los dos llegan con `amount_pen` a null.
        self.assertEqual(data['charge_currency'], 'PEN')

    def test_only_methods_that_move_soles_have_a_charge_currency_of_pen(self):
        self.assertTrue(charges_in_pen('yape'))
        self.assertFalse(charges_in_pen('paypal'))
        # Un método que este build no conoce cae en la moneda base, nunca en PEN: es
        # preferible no registrar conversión a registrar una inventada.
        self.assertFalse(charges_in_pen('bitcoin'))

    def test_notification_payload_carries_the_method(self):
        from unittest.mock import patch

        from apps.subscriptions.tasks import notify_payment_proof

        proof = self._make_proof(method='yape')
        with override_settings(
            N8N_PAYMENT_WEBHOOK_URL='https://n8n.example/hook', APP_BASE_URL='https://x',
        ), patch('apps.subscriptions.tasks.requests.post') as mock_post:
            mock_post.return_value.status_code = 200
            notify_payment_proof(str(proof.id))

        payload = mock_post.call_args.kwargs['json']
        self.assertEqual(payload['method'], 'yape')
        self.assertIn('transaction_reference', payload)


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestProofCreationPerMethod(APITestCase):
    """
    Las dos reglas que dependen del método al crear el comprobante, contra el endpoint
    real: qué se registra como testigo del cobro, y qué se exige para aceptarlo.
    """

    UPGRADE_URL = '/api/v1/admin/subscriptions/plan-upgrade/'

    def setUp(self):
        cache.clear()
        _set_method('yape', is_enabled=True, phone='999888777')
        _set_method('paypal', is_enabled=True, checkout_url='https://paypal.me/x')

        config = CurrencyConfig.get()
        config.usd_to_pen = Decimal('3.7500')
        config.save()

        self.tenant = _create_tenant(f'pay-{uuid.uuid4().hex[:8]}', plan='free')
        self.user = _create_superuser(self.tenant, f'owner-{uuid.uuid4().hex[:8]}@x.com')
        self.client.force_authenticate(user=self.user)
        Plan.objects.get_or_create(
            id='professional', defaults={'display_name': 'Professional', 'price_monthly': 79},
        )

    def _upgrade(self, **extra):
        data = {
            'plan': 'professional',
            'screenshot': SimpleUploadedFile(
                'proof.png', png_bytes(), content_type='image/png',
            ),
            **extra,
        }
        return self.client.post(
            self.UPGRADE_URL, data, format='multipart',
            **{'HTTP_X_TENANT_SLUG': self.tenant.slug},
        )

    def test_a_payment_in_soles_records_the_rate_it_was_paid_at(self):
        response = self._upgrade(method='yape')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        proof = PaymentProof.objects.get(id=response.data['proof_id'])
        self.assertEqual(proof.exchange_rate, Decimal('3.7500'))
        self.assertEqual(proof.amount_pen, Decimal('296.25'))

    def test_a_payment_in_dollars_records_no_conversion(self):
        # Anotarle «S/ 296.25» a quien pagó $79 por PayPal sería inventarle un importe
        # que nadie transfirió, y el panel lo mostraría como si fuera el del recibo.
        response = self._upgrade(method='paypal', transaction_reference='8XY12345AB')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        proof = PaymentProof.objects.get(id=response.data['proof_id'])
        self.assertIsNone(proof.exchange_rate)
        self.assertIsNone(proof.amount_pen)

    def test_paypal_without_a_transaction_reference_is_rejected(self):
        # El guardarraíl vive aquí y no solo en el formulario del Hub: uno que viva en
        # el cliente se salta llamando al endpoint directamente.
        response = self._upgrade(method='paypal')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('ID de transacción', response.data['detail'])
        self.assertFalse(PaymentProof.objects.exists())

    def test_the_reference_is_stored_with_the_proof(self):
        response = self._upgrade(method='paypal', transaction_reference='  8XY12345AB  ')

        proof = PaymentProof.objects.get(id=response.data['proof_id'])
        self.assertEqual(proof.transaction_reference, '8XY12345AB')

    def test_a_method_without_reference_is_not_asked_for_one(self):
        response = self._upgrade(method='yape')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            PaymentProof.objects.get(id=response.data['proof_id']).transaction_reference, '',
        )
