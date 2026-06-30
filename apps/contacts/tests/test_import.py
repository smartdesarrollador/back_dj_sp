"""
Tests for bulk contact import (POST /api/v1/app/contacts/import/).

Covers: feature gating, row creation + name split, partial plan-limit, invalid
rows reported without aborting, row cap, and audit logging.
"""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.audit.models import AuditLog
from apps.contacts.models import Contact
from apps.tenants.models import Tenant

User = get_user_model()

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

IMPORT_URL = '/api/v1/app/contacts/import/'


def _create_tenant(slug, plan='professional'):
    return Tenant.objects.create(name=slug.capitalize(), slug=slug, subdomain=slug, plan=plan)


def _create_superuser(tenant, email):
    user = User.objects.create_user(email=email, name='Test User', password='x', tenant=tenant)
    user.is_superuser = True
    user.save(update_fields=['is_superuser'])
    return user


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestContactImport(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = _create_tenant('import-corp', plan='professional')
        self.user = _create_superuser(self.tenant, 'u@import.com')
        self.client.force_authenticate(user=self.user)
        self.slug = {'HTTP_X_TENANT_SLUG': 'import-corp'}

    def test_import_creates_contacts_and_splits_name(self):
        items = [{'name': 'Ada Lovelace', 'email': 'ada@x.com'}, {'name': 'Alan Turing'}]
        r = self.client.post(IMPORT_URL, {'items': items}, format='json', **self.slug)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        body = r.json()
        self.assertEqual(body['created'], 2)
        self.assertEqual(body['skipped'], 0)
        self.assertEqual(body['errors'], [])
        ada = Contact.objects.get(tenant=self.tenant, user=self.user, email='ada@x.com')
        self.assertEqual(ada.first_name, 'Ada')
        self.assertEqual(ada.last_name, 'Lovelace')

    def test_feature_gate_blocks_free_plan(self):
        self.tenant.plan = 'free'
        self.tenant.save(update_fields=['plan'])
        r = self.client.post(IMPORT_URL, {'items': [{'name': 'X'}]}, format='json', **self.slug)
        self.assertNotEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn(r.status_code, (status.HTTP_402_PAYMENT_REQUIRED, status.HTTP_403_FORBIDDEN))

    def test_partial_plan_limit(self):
        # Starter: max_contacts = 100. Import 102 → 100 created, 2 skipped.
        self.tenant.plan = 'starter'
        self.tenant.save(update_fields=['plan'])
        items = [{'name': f'Contact {i}'} for i in range(102)]
        r = self.client.post(IMPORT_URL, {'items': items}, format='json', **self.slug)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        body = r.json()
        self.assertEqual(body['created'], 100)
        self.assertEqual(body['skipped'], 2)
        self.assertEqual(Contact.objects.filter(tenant=self.tenant, user=self.user).count(), 100)

    def test_invalid_rows_reported_without_aborting(self):
        items = [{'name': 'Valid Person'}, {'email': 'noname@x.com'}]  # 2nd lacks required name
        r = self.client.post(IMPORT_URL, {'items': items}, format='json', **self.slug)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        body = r.json()
        self.assertEqual(body['created'], 1)
        self.assertEqual(len(body['errors']), 1)
        self.assertEqual(body['errors'][0]['index'], 1)

    def test_row_cap(self):
        items = [{'name': 'x'} for _ in range(1001)]
        r = self.client.post(IMPORT_URL, {'items': items}, format='json', **self.slug)
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_import_is_audited(self):
        self.client.post(IMPORT_URL, {'items': [{'name': 'X'}]}, format='json', **self.slug)
        log = AuditLog.objects.filter(action='contacts.import', resource_type='Contact').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.extra['created'], 1)
