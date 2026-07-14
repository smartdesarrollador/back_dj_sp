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

    # ── List contacts (pagination) ───────────────────────────────────────────

    def _create_contacts(self, n, **overrides):
        contacts = []
        for i in range(n):
            defaults = {
                'tenant': self.tenant,
                'user': self.user,
                'first_name': f'Contact{i}',
                'last_name': f'Last{i}',
            }
            defaults.update(overrides)
            contacts.append(Contact.objects.create(**defaults))
        return contacts

    def test_list_contacts_without_page_preserves_legacy_shape(self):
        self._create_contacts(5)
        response = self.client.get(BASE_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body['results'], body['contacts'])
        self.assertEqual(body['count'], len(body['results']))
        self.assertEqual(len(body['contacts']), 5)

    def test_list_contacts_first_page_default_per_page(self):
        self._create_contacts(25)
        response = self.client.get(BASE_URL, {'page': 1}, **self.slug)
        body = response.json()
        self.assertEqual(len(body['contacts']), 20)
        self.assertEqual(body['pagination'], {'page': 1, 'per_page': 20, 'total': 25})

    def test_list_contacts_second_page(self):
        self._create_contacts(25)
        response = self.client.get(BASE_URL, {'page': 2}, **self.slug)
        body = response.json()
        self.assertEqual(len(body['contacts']), 5)
        self.assertEqual(body['pagination']['page'], 2)

    def test_list_contacts_custom_per_page(self):
        self._create_contacts(10)
        response = self.client.get(BASE_URL, {'page': 1, 'per_page': 5}, **self.slug)
        body = response.json()
        self.assertEqual(len(body['contacts']), 5)
        self.assertEqual(body['pagination']['per_page'], 5)

    def test_list_contacts_per_page_clamped_to_100(self):
        self._create_contacts(3)
        response = self.client.get(BASE_URL, {'page': 1, 'per_page': 500}, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['pagination']['per_page'], 100)

    def test_list_contacts_page_out_of_range_returns_empty(self):
        self._create_contacts(3)
        response = self.client.get(BASE_URL, {'page': 999}, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body['contacts'], [])
        self.assertEqual(body['pagination']['total'], 3)

    def test_list_contacts_invalid_page_falls_back_to_default(self):
        self._create_contacts(3)
        response = self.client.get(BASE_URL, {'page': 'abc'}, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['pagination']['page'], 1)

    def test_list_contacts_invalid_per_page_falls_back_to_default(self):
        self._create_contacts(3)
        response = self.client.get(BASE_URL, {'page': 1, 'per_page': 'xyz'}, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['pagination']['per_page'], 20)

    def test_list_contacts_negative_page_clamped_to_one(self):
        self._create_contacts(3)
        response = self.client.get(BASE_URL, {'page': -5}, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['pagination']['page'], 1)

    def test_list_contacts_filters_combined_with_pagination(self):
        group = ContactGroup.objects.create(tenant=self.tenant, user=self.user, name='VIP')
        self._create_contacts(3, group=group)
        self._create_contacts(4)
        response = self.client.get(
            BASE_URL, {'group': str(group.pk), 'page': 1, 'per_page': 2}, **self.slug
        )
        body = response.json()
        self.assertEqual(len(body['contacts']), 2)
        self.assertEqual(body['pagination']['total'], 3)
        self.assertTrue(all(c['group']['id'] == str(group.pk) for c in body['contacts']))

    def test_list_contacts_cross_tenant_pagination_isolated(self):
        other_tenant = _create_tenant('other-contacts-pagination')
        other_user = _create_superuser(other_tenant, 'other@contacts-pagination.com')
        Contact.objects.create(tenant=other_tenant, user=other_user, first_name='Other')
        self._create_contacts(2)
        response = self.client.get(BASE_URL, {'page': 1}, **self.slug)
        self.assertEqual(response.json()['pagination']['total'], 2)
