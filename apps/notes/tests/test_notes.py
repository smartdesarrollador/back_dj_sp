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

from apps.notes.models import Note, NoteCategory
from apps.sharing.models import Share
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
        category = NoteCategory.objects.create(tenant=self.tenant, user=self.user, name='Trabajo')
        data = {'title': 'My Note', 'content': 'Hello world', 'category': str(category.pk)}
        response = self.client.post(BASE_URL, data, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        body = response.json()
        self.assertEqual(body['title'], 'My Note')
        self.assertEqual(body['category']['name'], 'Trabajo')
        self.assertFalse(body['is_pinned'])
        self.assertTrue(Note.objects.filter(tenant=self.tenant, title='My Note').exists())

    def test_create_note_without_category(self):
        response = self.client.post(BASE_URL, {'title': 'No category'}, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(response.json()['category'])

    # ── Categories ────────────────────────────────────────────────────────────

    def test_create_note_category_success(self):
        data = {'name': 'Ideas', 'color': '#f59e0b'}
        response = self.client.post(BASE_URL + 'categories/', data, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        body = response.json()
        self.assertEqual(body['name'], 'Ideas')
        self.assertEqual(body['color'], '#f59e0b')
        self.assertEqual(body['notes_count'], 0)
        self.assertTrue(
            NoteCategory.objects.filter(tenant=self.tenant, name='Ideas').exists()
        )

    def test_list_note_categories_returns_categories(self):
        c1 = NoteCategory.objects.create(tenant=self.tenant, user=self.user, name='Trabajo', color='#3b82f6')
        NoteCategory.objects.create(tenant=self.tenant, user=self.user, name='Personal', color='#10b981')
        Note.objects.create(tenant=self.tenant, user=self.user, title='A', category=c1)
        response = self.client.get(BASE_URL + 'categories/', **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        categories = response.json()['categories']
        self.assertEqual(len(categories), 2)
        trabajo = next(c for c in categories if c['name'] == 'Trabajo')
        self.assertEqual(trabajo['notes_count'], 1)

    def test_delete_note_category_success(self):
        category = NoteCategory.objects.create(tenant=self.tenant, user=self.user, name='Trabajo')
        url = f'{BASE_URL}categories/{category.pk}/'
        response = self.client.delete(url, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(NoteCategory.objects.filter(pk=category.pk).exists())

    def test_deleting_category_nulls_note_category(self):
        category = NoteCategory.objects.create(tenant=self.tenant, user=self.user, name='Trabajo')
        note = Note.objects.create(tenant=self.tenant, user=self.user, title='A', category=category)
        url = f'{BASE_URL}categories/{category.pk}/'
        response = self.client.delete(url, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        note.refresh_from_db()
        self.assertIsNone(note.category)

    def test_filter_notes_by_category(self):
        c1 = NoteCategory.objects.create(tenant=self.tenant, user=self.user, name='Trabajo')
        c2 = NoteCategory.objects.create(tenant=self.tenant, user=self.user, name='Personal')
        Note.objects.create(tenant=self.tenant, user=self.user, title='A', category=c1)
        Note.objects.create(tenant=self.tenant, user=self.user, title='B', category=c2)
        response = self.client.get(f'{BASE_URL}?category={c1.pk}', **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        notes = response.json()['notes']
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]['title'], 'A')

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

    # ── Shared notes flagging ────────────────────────────────────────────────

    def test_own_note_is_not_shared(self):
        Note.objects.create(tenant=self.tenant, user=self.user, title='Mine')
        response = self.client.get(BASE_URL, **self.slug)
        note_data = response.json()['notes'][0]
        self.assertFalse(note_data['is_shared'])
        self.assertIsNone(note_data['shared_by_name'])

    def test_shared_note_is_flagged_with_sharer_name(self):
        owner = _create_superuser(self.tenant, 'owner2@notes.com')
        owner.name = 'Nota Owner'
        owner.save(update_fields=['name'])
        note = Note.objects.create(tenant=self.tenant, user=owner, title='Shared with me')
        Share.objects.create(
            tenant=self.tenant,
            resource_type='note',
            resource_id=note.id,
            shared_by=owner,
            shared_with=self.user,
            permission_level='viewer',
        )
        response = self.client.get(BASE_URL, **self.slug)
        note_data = next(n for n in response.json()['notes'] if n['id'] == str(note.id))
        self.assertTrue(note_data['is_shared'])
        self.assertEqual(note_data['shared_by_name'], 'Nota Owner')

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

    # ── Tags: normalization ──────────────────────────────────────────────────

    def test_create_note_normalizes_tags(self):
        data = {
            'title': 'Tagged',
            'tags': ['Urgente', ' urgente ', 'Trabajo', 'trabajo', ''],
        }
        response = self.client.post(BASE_URL, data, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.json()['tags'], ['urgente', 'trabajo'])

    def test_update_note_normalizes_tags(self):
        note = Note.objects.create(tenant=self.tenant, user=self.user, title='X')
        url = f'{BASE_URL}{note.pk}/'
        response = self.client.patch(
            url, {'tags': ['Cliente', ' cliente ', 'cliente']}, **self.slug
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['tags'], ['cliente'])

    # ── Tags: suggestions endpoint ───────────────────────────────────────────

    def test_note_tags_endpoint_returns_distinct_sorted(self):
        Note.objects.create(tenant=self.tenant, user=self.user, title='A', tags=['zebra', 'apple'])
        Note.objects.create(tenant=self.tenant, user=self.user, title='B', tags=['apple', 'mango'])
        response = self.client.get(f'{BASE_URL}tags/', **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['tags'], ['apple', 'mango', 'zebra'])

    def test_note_tags_endpoint_scoped_to_user(self):
        Note.objects.create(tenant=self.tenant, user=self.user, title='Mine', tags=['mine'])
        other_user = _create_superuser(self.tenant, 'other-user@notes.com')
        Note.objects.create(tenant=self.tenant, user=other_user, title='Theirs', tags=['theirs'])
        response = self.client.get(f'{BASE_URL}tags/', **self.slug)
        self.assertEqual(response.json()['tags'], ['mine'])

    def test_note_tags_endpoint_empty_state(self):
        response = self.client.get(f'{BASE_URL}tags/', **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['tags'], [])

    def test_import_notes_normalizes_tags(self):
        items = [{'title': 'Imported', 'tags': ['Urgente', 'urgente', ' Trabajo ']}]
        response = self.client.post(
            f'{BASE_URL}import/', {'items': items}, format='json', **self.slug
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        note = Note.objects.get(tenant=self.tenant, title='Imported')
        self.assertEqual(note.tags, ['urgente', 'trabajo'])

    # ── Tags: filtering ──────────────────────────────────────────────────────

    def test_filter_notes_by_tag(self):
        Note.objects.create(tenant=self.tenant, user=self.user, title='A', tags=['urgente'])
        Note.objects.create(tenant=self.tenant, user=self.user, title='B', tags=['personal'])
        response = self.client.get(f'{BASE_URL}?tag=urgente', **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        notes = response.json()['notes']
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]['title'], 'A')

    def test_filter_notes_by_tag_no_match_returns_empty(self):
        Note.objects.create(tenant=self.tenant, user=self.user, title='A', tags=['urgente'])
        response = self.client.get(f'{BASE_URL}?tag=inexistente', **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['notes'], [])

    # ── List notes (pagination) ──────────────────────────────────────────────

    def _create_notes(self, n, **overrides):
        notes = []
        for i in range(n):
            defaults = {
                'tenant': self.tenant,
                'user': self.user,
                'title': f'Note {i}',
            }
            defaults.update(overrides)
            notes.append(Note.objects.create(**defaults))
        return notes

    def test_list_notes_without_page_preserves_legacy_shape(self):
        self._create_notes(2, is_pinned=True)
        self._create_notes(3, is_pinned=False)
        response = self.client.get(BASE_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body['results'], body['notes'])
        self.assertEqual(body['count'], len(body['results']))
        self.assertEqual(len(body['notes']), 5)

    def test_list_notes_with_page_includes_all_pinned_plus_page_of_unpinned(self):
        self._create_notes(3, is_pinned=True)
        self._create_notes(25, is_pinned=False)
        response = self.client.get(BASE_URL, {'page': 1}, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(len(body['notes']), 3 + 20)
        self.assertEqual(body['pagination'], {'page': 1, 'per_page': 20, 'total': 25})
        self.assertEqual(sum(1 for n in body['notes'] if n['is_pinned']), 3)

    def test_list_notes_second_page_returns_remaining_unpinned_plus_all_pinned(self):
        self._create_notes(3, is_pinned=True)
        self._create_notes(25, is_pinned=False)
        response = self.client.get(BASE_URL, {'page': 2}, **self.slug)
        body = response.json()
        self.assertEqual(len(body['notes']), 3 + 5)
        self.assertEqual(body['pagination']['page'], 2)
        self.assertEqual(sum(1 for n in body['notes'] if n['is_pinned']), 3)

    def test_list_notes_more_than_20_pinned_all_returned_without_page(self):
        self._create_notes(25, is_pinned=True)
        response = self.client.get(BASE_URL, **self.slug)
        self.assertEqual(len(response.json()['notes']), 25)

    def test_list_notes_more_than_20_pinned_all_returned_with_page(self):
        self._create_notes(25, is_pinned=True)
        response = self.client.get(BASE_URL, {'page': 1}, **self.slug)
        body = response.json()
        self.assertEqual(len(body['notes']), 25)
        self.assertEqual(body['pagination']['total'], 0)

    def test_list_notes_custom_per_page(self):
        self._create_notes(10, is_pinned=False)
        response = self.client.get(BASE_URL, {'page': 1, 'per_page': 5}, **self.slug)
        body = response.json()
        self.assertEqual(len(body['notes']), 5)
        self.assertEqual(body['pagination']['per_page'], 5)

    def test_list_notes_per_page_clamped_to_100(self):
        self._create_notes(3, is_pinned=False)
        response = self.client.get(BASE_URL, {'page': 1, 'per_page': 500}, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['pagination']['per_page'], 100)

    def test_list_notes_invalid_page_falls_back_to_default(self):
        self._create_notes(3, is_pinned=False)
        response = self.client.get(BASE_URL, {'page': 'abc'}, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['pagination']['page'], 1)

    def test_list_notes_invalid_per_page_falls_back_to_default(self):
        self._create_notes(3, is_pinned=False)
        response = self.client.get(BASE_URL, {'page': 1, 'per_page': 'xyz'}, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['pagination']['per_page'], 20)

    def test_list_notes_negative_page_clamped_to_one(self):
        self._create_notes(3, is_pinned=False)
        response = self.client.get(BASE_URL, {'page': -5}, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['pagination']['page'], 1)

    def test_list_notes_page_out_of_range_returns_only_pinned(self):
        self._create_notes(2, is_pinned=True)
        self._create_notes(3, is_pinned=False)
        response = self.client.get(BASE_URL, {'page': 999}, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(len(body['notes']), 2)
        self.assertTrue(all(n['is_pinned'] for n in body['notes']))
        self.assertEqual(body['pagination']['total'], 3)

    def test_list_notes_filters_combined_with_pagination(self):
        category = NoteCategory.objects.create(tenant=self.tenant, user=self.user, name='Trabajo')
        self._create_notes(1, is_pinned=True, category=category)
        self._create_notes(1, is_pinned=True)
        self._create_notes(2, is_pinned=False, category=category)
        self._create_notes(2, is_pinned=False)
        response = self.client.get(
            BASE_URL, {'category': str(category.pk), 'page': 1, 'per_page': 20}, **self.slug
        )
        body = response.json()
        self.assertEqual(len(body['notes']), 1 + 2)
        self.assertEqual(body['pagination']['total'], 2)
        self.assertTrue(all(n['category']['id'] == str(category.pk) for n in body['notes']))

    def test_list_notes_cross_tenant_pagination_isolated(self):
        other_tenant = _create_tenant('other-notes-pagination')
        other_user = _create_superuser(other_tenant, 'other@notes-pagination.com')
        Note.objects.create(tenant=other_tenant, user=other_user, title='Other tenant note')
        self._create_notes(2, is_pinned=True)
        self._create_notes(2, is_pinned=False)
        response = self.client.get(BASE_URL, {'page': 1}, **self.slug)
        body = response.json()
        self.assertEqual(body['pagination']['total'], 2)
        self.assertEqual(len(body['notes']), 4)

    def test_list_notes_shared_pinned_note_included_in_pinned_bucket(self):
        owner = _create_superuser(self.tenant, 'owner-pinned@notes.com')
        shared_note = Note.objects.create(
            tenant=self.tenant, user=owner, title='Shared pinned', is_pinned=True
        )
        Share.objects.create(
            tenant=self.tenant,
            resource_type='note',
            resource_id=shared_note.id,
            shared_by=owner,
            shared_with=self.user,
            permission_level='viewer',
        )
        response = self.client.get(BASE_URL, {'page': 1}, **self.slug)
        body = response.json()
        shared_ids = {n['id'] for n in body['notes'] if n['is_pinned']}
        self.assertIn(str(shared_note.id), shared_ids)
