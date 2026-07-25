"""
Tests de `GET /api/v1/auth/payment-token-status`.

El Hub lo consulta al rehidratar el paso de pago del registro (tras un refresco o un
back) para avisar de un token muerto **antes** de que el cliente suba el comprobante.
Lo que se protege aquí: que distinga vivo de muerto, y que no filtre nada del tenant a
quien solo presenta un token.
"""
import uuid
from unittest.mock import patch

from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.auth_app.tokens import (
    consume_payment_upload_token,
    create_payment_upload_token,
)
from apps.subscriptions.models import Plan

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

STATUS_URL   = '/api/v1/auth/payment-token-status'
REGISTER_URL = '/api/v1/auth/register'


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class PaymentTokenStatusTests(APITestCase):
    def setUp(self):
        cache.clear()
        Plan.objects.create(id='starter', display_name='Starter', price_monthly=19)

    def _register_paid(self) -> str:
        uid = uuid.uuid4().hex[:6]
        with patch('apps.auth_app.views.send_mail', return_value=1):
            response = self.client.post(
                REGISTER_URL,
                {
                    'name': 'Test User',
                    'email': f'u-{uid}@test.com',
                    'password': 'SecurePass1!',
                    'organization_name': f'Org {uid}',
                    'plan': 'starter',
                },
                format='json',
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        return response.data['payment_upload_token']

    def test_token_recien_emitido_es_valido(self):
        token = self._register_paid()

        response = self.client.get(STATUS_URL, {'token': token})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['valid'])
        self.assertGreater(response.data['expires_in'], 0)

    def test_token_inexistente_no_es_valido(self):
        response = self.client.get(STATUS_URL, {'token': 'no-existe'})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data['valid'])
        self.assertIsNone(response.data['expires_in'])

    def test_token_ya_consumido_no_es_valido(self):
        # Tras enviar el comprobante el token se quema: rehidratar no debe reabrir el paso.
        token = self._register_paid()
        consume_payment_upload_token(token)

        response = self.client.get(STATUS_URL, {'token': token})

        self.assertFalse(response.data['valid'])

    def test_sin_token_es_400(self):
        response = self.client.get(STATUS_URL)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_no_filtra_nada_del_tenant(self):
        # Quien pregunta no está autenticado: la respuesta solo puede decir si el token
        # sirve y cuánto le queda, nunca de quién es.
        token = self._register_paid()

        response = self.client.get(STATUS_URL, {'token': token})

        self.assertEqual(set(response.data.keys()), {'valid', 'expires_in'})

    def test_no_requiere_autenticacion(self):
        # El cliente aún no puede loguearse: su email no está verificado.
        token = create_payment_upload_token(str(uuid.uuid4()))

        response = self.client.get(STATUS_URL, {'token': token})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['valid'])
