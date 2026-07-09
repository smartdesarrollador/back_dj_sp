"""
Tests for PASO 12 — Contacts module.
Covers: list, create, plan limit, group feature gate, CSV export feature gate.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.contacts.models import Contact, ContactGroup
from apps.sharing.models import Share
from apps.tenants.models import Tenant

User = get_user_model()

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

BASE_URL = '/api/v1/app/contacts/'


def _create_tenant(slug, plan='professional'):
    return Tenant.objects.create(name=slug.capitalize(), slug=slug, subdomain=slug, plan=plan)


def _create_superuser(tenant, email):
    user = User.objects.create_user(
        email=email, name='Test User', password='x', tenant=tenant
    )
    user.is_superuser = True
    user.save(update_fields=['is_superuser'])
    return user


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestContactViews(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = _create_tenant('contacts-corp')
        self.user = _create_superuser(self.tenant, 'u@contacts.com')
        self.client.force_authenticate(user=self.user)
        self.slug = {'HTTP_X_TENANT_SLUG': 'contacts-corp'}

    # ── List ──────────────────────────────────────────────────────────────────

    def test_list_contacts_empty(self):
        response = self.client.get(BASE_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['contacts'], [])

    # ── Create ────────────────────────────────────────────────────────────────

    def test_create_contact_success(self):
        data = {'name': 'Jane Doe', 'email': 'jane@example.com'}
        response = self.client.post(BASE_URL, data, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        body = response.json()
        self.assertEqual(body['first_name'], 'Jane')
        self.assertEqual(body['email'], 'jane@example.com')
        self.assertTrue(Contact.objects.filter(tenant=self.tenant, email='jane@example.com').exists())

    # ── Plan limit ────────────────────────────────────────────────────────────

    def test_create_contact_exceeds_plan_limit(self):
        with patch('apps.contacts.views.check_plan_limit') as mock_limit:
            from core.exceptions import PlanLimitExceeded
            mock_limit.side_effect = PlanLimitExceeded()
            data = {'first_name': 'X'}
            response = self.client.post(BASE_URL, data, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)

    # ── Shared contacts flagging ──────────────────────────────────────────────

    def test_shared_contact_is_flagged_with_sharer_name(self):
        owner = _create_superuser(self.tenant, 'owner2@contacts.com')
        owner.name = 'Contact Owner'
        owner.save(update_fields=['name'])
        contact = Contact.objects.create(
            tenant=self.tenant, user=owner, first_name='Shared', last_name='Contact'
        )
        Share.objects.create(
            tenant=self.tenant,
            resource_type='contact',
            resource_id=contact.id,
            shared_by=owner,
            shared_with=self.user,
            permission_level='viewer',
        )
        response = self.client.get(BASE_URL, **self.slug)
        data = next(c for c in response.json()['contacts'] if c['id'] == str(contact.id))
        self.assertTrue(data['is_shared'])
        self.assertEqual(data['shared_by_name'], 'Contact Owner')

    # ── Group feature gate ─────────────────────────────────────────────────────

    def test_contact_group_requires_feature(self):
        """Free plan cannot access contact groups endpoint."""
        free_tenant = _create_tenant('free-contacts', plan='free')
        free_user = _create_superuser(free_tenant, 'free@contacts.com')
        self.client.force_authenticate(user=free_user)
        response = self.client.get(
            BASE_URL + 'groups/', HTTP_X_TENANT_SLUG='free-contacts'
        )
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)

    def test_create_contact_group_requires_feature(self):
        free_tenant = _create_tenant('free-groups', plan='free')
        free_user = _create_superuser(free_tenant, 'free@groups.com')
        self.client.force_authenticate(user=free_user)
        response = self.client.post(
            BASE_URL + 'groups/', {'name': 'VIP'}, HTTP_X_TENANT_SLUG='free-groups'
        )
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)

    def test_create_contact_group_success(self):
        data = {'name': 'VIP', 'color': '#2563eb'}
        response = self.client.post(BASE_URL + 'groups/', data, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        body = response.json()
        self.assertEqual(body['name'], 'VIP')
        self.assertEqual(body['color'], '#2563eb')
        self.assertEqual(body['contacts_count'], 0)
        self.assertTrue(
            ContactGroup.objects.filter(tenant=self.tenant, name='VIP').exists()
        )

    def test_list_contact_groups_returns_groups(self):
        g1 = ContactGroup.objects.create(tenant=self.tenant, user=self.user, name='VIP', color='#2563eb')
        ContactGroup.objects.create(tenant=self.tenant, user=self.user, name='Leads', color='#16a34a')
        Contact.objects.create(
            tenant=self.tenant, user=self.user, first_name='Jane', last_name='Doe', group=g1
        )
        response = self.client.get(BASE_URL + 'groups/', **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        groups = response.json()['groups']
        self.assertEqual(len(groups), 2)
        vip = next(g for g in groups if g['name'] == 'VIP')
        self.assertEqual(vip['contacts_count'], 1)

    def test_delete_contact_group_success(self):
        group = ContactGroup.objects.create(tenant=self.tenant, user=self.user, name='VIP')
        url = f'{BASE_URL}groups/{group.pk}/'
        response = self.client.delete(url, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(ContactGroup.objects.filter(pk=group.pk).exists())

    def test_deleting_group_nulls_contact_group(self):
        group = ContactGroup.objects.create(tenant=self.tenant, user=self.user, name='VIP')
        contact = Contact.objects.create(
            tenant=self.tenant, user=self.user, first_name='Jane', last_name='Doe', group=group
        )
        url = f'{BASE_URL}groups/{group.pk}/'
        response = self.client.delete(url, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        contact.refresh_from_db()
        self.assertIsNone(contact.group)

    # ── Export feature gate ────────────────────────────────────────────────────

    def test_export_csv_requires_feature(self):
        """Free plan cannot export; Starter+ gets CSV response."""
        # Free plan → 403
        free_tenant = _create_tenant('free-exp', plan='free')
        free_user = _create_superuser(free_tenant, 'free@exp.com')
        self.client.force_authenticate(user=free_user)
        response = self.client.get(BASE_URL + 'export/', HTTP_X_TENANT_SLUG='free-exp')
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)

        # Starter plan → 200 CSV
        starter_tenant = _create_tenant('starter-exp', plan='starter')
        starter_user = _create_superuser(starter_tenant, 'starter@exp.com')
        self.client.force_authenticate(user=starter_user)
        response = self.client.get(BASE_URL + 'export/', HTTP_X_TENANT_SLUG='starter-exp')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('text/csv', response.get('Content-Type', ''))
