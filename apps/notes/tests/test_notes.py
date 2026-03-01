"""
Tests for PASO 12 — Notes module.
Covers: list, create, plan limit, pin toggle, cross-tenant isolation.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.notes.models import Note
from apps.tenants.models import Tenant

User = get_user_model()

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

BASE_URL = '/api/v1/app/notes/'


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
class TestNoteViews(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = _create_tenant('notes-corp')
        self.user = _create_superuser(self.tenant, 'u@notes.com')
        self.client.force_authenticate(user=self.user)
        self.slug = {'HTTP_X_TENANT_SLUG': 'notes-corp'}

    # ── List ──────────────────────────────────────────────────────────────────

    def test_list_notes_empty(self):
        response = self.client.get(BASE_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['notes'], [])

    # ── Create ────────────────────────────────────────────────────────────────

    def test_create_note_success(self):
        data = {'title': 'My Note', 'content': 'Hello world', 'category': 'work'}
        response = self.client.post(BASE_URL, data, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        body = response.json()
        self.assertEqual(body['title'], 'My Note')
        self.assertEqual(body['category'], 'work')
        self.assertFalse(body['is_pinned'])
        self.assertTrue(Note.objects.filter(tenant=self.tenant, title='My Note').exists())

    # ── Plan limit ────────────────────────────────────────────────────────────

    def test_create_note_exceeds_plan_limit(self):
        with patch('apps.notes.views.check_plan_limit') as mock_limit:
            from core.exceptions import PlanLimitExceeded
            mock_limit.side_effect = PlanLimitExceeded()
            response = self.client.post(BASE_URL, {'title': 'X'}, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)

    # ── Pin toggle ────────────────────────────────────────────────────────────

    def test_note_pin_toggle(self):
        note = Note.objects.create(
            tenant=self.tenant, user=self.user, title='Pin Me', is_pinned=False
        )
        url = f'{BASE_URL}{note.pk}/pin/'
        response = self.client.patch(url, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        note.refresh_from_db()
        self.assertTrue(note.is_pinned)
        # Toggle back
        response2 = self.client.patch(url, **self.slug)
        self.assertEqual(response2.status_code, status.HTTP_200_OK)
        note.refresh_from_db()
        self.assertFalse(note.is_pinned)

    # ── Cross-tenant isolation ────────────────────────────────────────────────

    def test_cross_tenant_note_blocked(self):
        other_tenant = _create_tenant('other-notes')
        other_user = _create_superuser(other_tenant, 'other@notes.com')
        note = Note.objects.create(
            tenant=other_tenant, user=other_user, title='Other note'
        )
        url = f'{BASE_URL}{note.pk}/'
        response = self.client.get(url, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
