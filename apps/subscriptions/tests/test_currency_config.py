"""
Tests de la configuración de moneda de plataforma (CurrencyConfig).

Cubre el endpoint público que consumirá el Hub, el endpoint admin con su
guardarraíl de rango, y —lo más importante de esta fase— la NO-REGRESIÓN de los
contratos heredados que hoy sirven el tipo de cambio desde YapeConfig: mover la
fuente de verdad tiene que ser invisible para el Hub, el Admin y n8n.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.audit.models import AuditLog
from apps.subscriptions.models import CurrencyConfig, YapeConfig
from apps.tenants.models import Tenant
from utils.currency import get_exchange_rate

User = get_user_model()

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

ADMIN_CURRENCY_URL = '/api/v1/admin/billing/currency/'
PUBLIC_CURRENCY_URL = '/api/v1/public/currency/'
LEGACY_YAPE_PUBLIC_URL = '/api/v1/public/yape-payment/config/'
LEGACY_YAPE_ADMIN_URL = '/api/v1/admin/yape/config/'


def _create_tenant(slug, plan='professional'):
    return Tenant.objects.create(name=slug.capitalize(), slug=slug, subdomain=slug, plan=plan)


def _create_superuser(tenant, email):
    return User.objects.create_user(
        email=email, name='Owner', password='pass123', tenant=tenant,
        is_superuser=True, is_staff=True,
    )


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestPublicCurrencyEndpoint(APITestCase):
    def setUp(self):
        cache.clear()
        CurrencyConfig.objects.update_or_create(
            pk=1, defaults={'usd_to_pen': Decimal('3.7500')}
        )

    def test_returns_contract_shape(self):
        response = self.client.get(PUBLIC_CURRENCY_URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(
            set(body.keys()),
            {'base_currency', 'supported_currencies', 'rates',
             'default_display_currency', 'updated_at'},
        )
        self.assertEqual(body['base_currency'], 'USD')
        self.assertEqual(body['supported_currencies'], ['USD', 'PEN'])
        # USD explícito: el Hub convierte con amount * rates[c] sin caso especial.
        self.assertEqual(body['rates']['USD'], '1.0000')
        self.assertEqual(body['rates']['PEN'], '3.7500')

    def test_default_display_currency_is_usd(self):
        """Decisión de negocio: se muestra en dólares salvo que el usuario elija."""
        response = self.client.get(PUBLIC_CURRENCY_URL)
        self.assertEqual(response.json()['default_display_currency'], 'USD')

    def test_requires_no_auth(self):
        response = self.client.get(PUBLIC_CURRENCY_URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_does_not_create_rows(self):
        """Un GET no debe escribir: si falta la fila, degrada a los defaults."""
        CurrencyConfig.objects.all().delete()
        cache.clear()

        response = self.client.get(PUBLIC_CURRENCY_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['rates']['PEN'], '3.7500')
        self.assertEqual(CurrencyConfig.objects.count(), 0)


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestAdminCurrencyConfig(APITestCase):
    def setUp(self):
        cache.clear()
        CurrencyConfig.objects.update_or_create(
            pk=1, defaults={'usd_to_pen': Decimal('3.7500')}
        )
        self.tenant = _create_tenant('currency-corp')
        self.owner = _create_superuser(self.tenant, 'owner@currency-corp.com')
        self.client.force_authenticate(user=self.owner)
        self.headers = {'HTTP_X_TENANT_SLUG': 'currency-corp'}

    def test_get_returns_config(self):
        response = self.client.get(ADMIN_CURRENCY_URL, **self.headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        currency = response.json()['currency']
        self.assertEqual(currency['usd_to_pen'], '3.7500')
        self.assertEqual(currency['source'], 'manual')
        self.assertEqual(currency['default_display_currency'], 'USD')

    def test_patch_updates_rate_and_records_author(self):
        response = self.client.patch(
            ADMIN_CURRENCY_URL, {'usd_to_pen': '3.9000'}, format='json', **self.headers
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        cfg = CurrencyConfig.objects.get(pk=1)
        self.assertEqual(cfg.usd_to_pen, Decimal('3.9000'))
        self.assertEqual(cfg.updated_by, self.owner)

    def test_patch_rejects_out_of_range_rate_with_readable_message(self):
        """
        El dedazo real es 375 en vez de 3.75. El assert va sobre el cuerpo HTTP,
        no sobre la excepción: una validación no está verificada hasta comprobar
        que el cliente recibe el motivo (LL-104).
        """
        response = self.client.patch(
            ADMIN_CURRENCY_URL, {'usd_to_pen': '375'}, format='json', **self.headers
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        body = str(response.json())
        self.assertIn('fuera del rango', body)
        self.assertNotIn('Validation error', body)
        # No se guardó nada
        self.assertEqual(CurrencyConfig.objects.get(pk=1).usd_to_pen, Decimal('3.7500'))

    def test_patch_rejects_zero_and_negative(self):
        for bad in ('0', '-3.75'):
            with self.subTest(value=bad):
                response = self.client.patch(
                    ADMIN_CURRENCY_URL, {'usd_to_pen': bad}, format='json', **self.headers
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_patch_rejects_unknown_display_currency(self):
        response = self.client.patch(
            ADMIN_CURRENCY_URL, {'default_display_currency': 'EUR'},
            format='json', **self.headers,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_patch_accepts_pen_as_default_display_currency(self):
        response = self.client.patch(
            ADMIN_CURRENCY_URL, {'default_display_currency': 'PEN'},
            format='json', **self.headers,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(CurrencyConfig.objects.get(pk=1).default_display_currency, 'PEN')

    def test_default_currency_change_reaches_the_public_endpoint(self):
        # Es lo que decide en qué moneda arranca el Hub para quien no ha elegido:
        # si la caché no se invalida, el cambio no se ve hasta 5 min después.
        self.client.patch(
            ADMIN_CURRENCY_URL, {'default_display_currency': 'PEN'},
            format='json', **self.headers,
        )
        self.client.force_authenticate(user=None)

        response = self.client.get(PUBLIC_CURRENCY_URL)

        self.assertEqual(response.json()['default_display_currency'], 'PEN')

    def test_default_currency_change_is_audited(self):
        self.client.patch(
            ADMIN_CURRENCY_URL, {'default_display_currency': 'PEN'},
            format='json', **self.headers,
        )

        entry = AuditLog.objects.filter(resource_type='currency_config').first()
        self.assertIsNotNone(entry)
        self.assertIn('default_display_currency', entry.extra['fields'])

    def test_patch_forces_source_manual(self):
        """Un cliente no puede mentir sobre el origen del dato."""
        CurrencyConfig.objects.filter(pk=1).update(source='auto')
        response = self.client.patch(
            ADMIN_CURRENCY_URL, {'source': 'auto', 'usd_to_pen': '3.8000'},
            format='json', **self.headers,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(CurrencyConfig.objects.get(pk=1).source, 'manual')

    def test_patch_writes_audit_log(self):
        self.client.patch(
            ADMIN_CURRENCY_URL, {'usd_to_pen': '3.9000'}, format='json', **self.headers
        )
        entry = AuditLog.objects.filter(resource_type='currency_config').first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.action, 'update')
        self.assertEqual(entry.extra['usd_to_pen_before'], '3.7500')
        self.assertEqual(entry.extra['usd_to_pen_after'], '3.9000')

    def test_patch_invalidates_cache_immediately(self):
        get_exchange_rate('PEN')  # calienta la caché

        self.client.patch(
            ADMIN_CURRENCY_URL, {'usd_to_pen': '4.1000'}, format='json', **self.headers
        )

        # Sin cache.clear(): si save() no invalidara, aquí seguiría 3.75
        self.assertEqual(get_exchange_rate('PEN'), Decimal('4.1000'))

    def test_public_endpoint_reflects_admin_change(self):
        self.client.patch(
            ADMIN_CURRENCY_URL, {'usd_to_pen': '4.1000'}, format='json', **self.headers
        )
        self.client.force_authenticate(user=None)

        response = self.client.get(PUBLIC_CURRENCY_URL)

        self.assertEqual(response.json()['rates']['PEN'], '4.1000')

    def test_requires_staff(self):
        regular = User.objects.create_user(
            email='member@currency-corp.com', name='Member', password='pass123',
            tenant=self.tenant,
        )
        self.client.force_authenticate(user=regular)
        response = self.client.patch(
            ADMIN_CURRENCY_URL, {'usd_to_pen': '4.0000'}, format='json', **self.headers
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestLegacyYapeContractUnchanged(APITestCase):
    """
    El tipo de cambio cambió de sitio; los contratos que ya lo servían NO.
    Estos son los canarios de "nada cambia" para el Hub, el Admin y n8n.
    """

    def setUp(self):
        cache.clear()
        CurrencyConfig.objects.update_or_create(
            pk=1, defaults={'usd_to_pen': Decimal('3.7500')}
        )
        self.tenant = _create_tenant('yape-corp')
        self.owner = _create_superuser(self.tenant, 'owner@yape-corp.com')
        self.headers = {'HTTP_X_TENANT_SLUG': 'yape-corp'}

    def test_public_config_shape_and_format_unchanged(self):
        response = self.client.get(LEGACY_YAPE_PUBLIC_URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(
            set(body.keys()),
            {'phone', 'holder_name', 'is_enabled', 'exchange_rate', 'instructions_note'},
        )
        # 2 decimales, como siempre: el Hub y n8n hacen parseFloat, pero el Admin
        # lo pinta tal cual en un input con step="0.01".
        self.assertEqual(body['exchange_rate'], '3.75')

    def test_legacy_endpoint_reads_from_currency_config(self):
        """La columna vieja ya no manda: se ignora aunque tenga otro valor."""
        CurrencyConfig.objects.filter(pk=1).update(usd_to_pen=Decimal('4.1000'))
        YapeConfig.objects.update_or_create(pk=1, defaults={'exchange_rate': Decimal('3.75')})
        cache.clear()

        response = self.client.get(LEGACY_YAPE_PUBLIC_URL)

        self.assertEqual(response.json()['exchange_rate'], '4.10')

    def test_legacy_patch_writes_currency_config_and_shadow(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.patch(
            LEGACY_YAPE_ADMIN_URL, {'exchange_rate': '3.90'}, format='json', **self.headers
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['exchange_rate'], '3.90')
        self.assertEqual(CurrencyConfig.objects.get(pk=1).usd_to_pen, Decimal('3.9000'))
        # Dual-write: la columna heredada queda sincronizada para que ninguna
        # lectura SQL/dump vea un valor obsoleto.
        self.assertEqual(YapeConfig.objects.get(pk=1).exchange_rate, Decimal('3.90'))

    def test_legacy_patch_rejects_out_of_range(self):
        """El único escritor que existe hoy tampoco puede meter un 375."""
        self.client.force_authenticate(user=self.owner)

        response = self.client.patch(
            LEGACY_YAPE_ADMIN_URL, {'exchange_rate': '375'}, format='json', **self.headers
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('fuera del rango', str(response.json()))
        self.assertEqual(CurrencyConfig.objects.get(pk=1).usd_to_pen, Decimal('3.7500'))

    def test_legacy_patch_still_updates_other_fields(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.patch(
            LEGACY_YAPE_ADMIN_URL,
            {'phone': '955 365 043', 'holder_name': 'Juan Pérez'},
            format='json', **self.headers,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['phone'], '955 365 043')
        self.assertEqual(YapeConfig.objects.get(pk=1).holder_name, 'Juan Pérez')
