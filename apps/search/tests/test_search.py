"""
Tests for the global search aggregator.
Covers: per-type matches, min-length validation, type filtering, date filtering,
cross-tenant isolation, and chat membership isolation.
"""
import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.bookmarks.models import Bookmark
from apps.calendar_app.models import CalendarEvent
from apps.chat.models import Conversation, ConversationMember, Message
from apps.contacts.models import Contact
from apps.notes.models import Note
from apps.projects.models import Project
from apps.snippets.models import CodeSnippet
from apps.tasks.models import Task, TaskBoard
from apps.tenants.models import Tenant
from apps.vault.models import VaultItem

User = get_user_model()

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

BASE_URL = '/api/v1/app/search/'


def _create_tenant(slug, plan='professional'):
    return Tenant.objects.create(name=slug.capitalize(), slug=slug, subdomain=slug, plan=plan)


def _create_superuser(tenant, email):
    user = User.objects.create_user(email=email, name='Test User', password='x', tenant=tenant)
    user.is_superuser = True
    user.save(update_fields=['is_superuser'])
    return user


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestGlobalSearch(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = _create_tenant('search-corp')
        self.user = _create_superuser(self.tenant, 'u@search.com')
        self.client.force_authenticate(user=self.user)
        self.slug = {'HTTP_X_TENANT_SLUG': 'search-corp'}

    def _seed(self):
        Note.objects.create(tenant=self.tenant, user=self.user, title='Alpha note', content='about mango')
        board = TaskBoard.objects.create(tenant=self.tenant, name='Board', created_by=self.user)
        Task.objects.create(tenant=self.tenant, board=board, title='mango task', created_by=self.user)
        now = timezone.now()
        CalendarEvent.objects.create(
            tenant=self.tenant, user=self.user, title='mango meeting',
            start_datetime=now, end_datetime=now + timedelta(hours=1),
        )
        Contact.objects.create(tenant=self.tenant, user=self.user, first_name='Mango', last_name='Person')
        Bookmark.objects.create(tenant=self.tenant, user=self.user, title='mango site', url='https://m.com')
        CodeSnippet.objects.create(tenant=self.tenant, user=self.user, title='mango snippet', code='print(1)')
        Project.objects.create(tenant=self.tenant, created_by=self.user, name='mango project')
        VaultItem.objects.create(
            tenant=self.tenant, user=self.user, title='mango login',
            item_type='login', data_ciphertext='ENCRYPTED_SECRET',
        )

    # ── Per-type matches ────────────────────────────────────────────────────

    def test_matches_across_all_types(self):
        self._seed()
        response = self.client.get(BASE_URL, {'q': 'mango'}, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body['total'], 8)
        found_types = {g['type'] for g in body['groups']}
        self.assertEqual(
            found_types,
            {'notes', 'tasks', 'events', 'contacts', 'bookmarks', 'snippets', 'projects', 'vault'},
        )

    def test_snippet_contains_match(self):
        Note.objects.create(tenant=self.tenant, user=self.user, title='T', content='lorem mango ipsum')
        body = self.client.get(BASE_URL, {'q': 'mango'}, **self.slug).json()
        note_group = next(g for g in body['groups'] if g['type'] == 'notes')
        self.assertIn('mango', note_group['results'][0]['snippet'])

    def test_projects_match_name_and_description(self):
        Project.objects.create(tenant=self.tenant, created_by=self.user, name='Mango migration')
        Project.objects.create(
            tenant=self.tenant, created_by=self.user, name='Other', description='handles mango imports'
        )
        body = self.client.get(BASE_URL, {'q': 'mango', 'types': 'projects'}, **self.slug).json()
        self.assertEqual(body['total'], 2)

    # ── Vault (title only, never ciphertext) ──────────────────────────────────

    def test_vault_matches_title(self):
        VaultItem.objects.create(
            tenant=self.tenant, user=self.user, title='Mango AWS key',
            item_type='api_key', data_ciphertext='ENCRYPTED',
        )
        body = self.client.get(BASE_URL, {'q': 'mango', 'types': 'vault'}, **self.slug).json()
        self.assertEqual(body['total'], 1)
        item = body['groups'][0]['results'][0]
        self.assertEqual(item['title'], 'Mango AWS key')
        self.assertEqual(item['snippet'], 'API Key')  # non-sensitive type label

    def test_vault_does_not_search_or_expose_ciphertext(self):
        # The secret content contains the term, but title does not → no match,
        # and the ciphertext must never appear in the response.
        VaultItem.objects.create(
            tenant=self.tenant, user=self.user, title='Boring title',
            item_type='login', data_ciphertext='secret mango password',
        )
        body = self.client.get(BASE_URL, {'q': 'mango', 'types': 'vault'}, **self.slug).json()
        self.assertEqual(body['total'], 0)
        # The secret ciphertext content must never appear in the response
        # ('mango' itself is echoed back in the `query` field, so we assert on
        # the rest of the secret instead).
        self.assertNotIn('password', json.dumps(body))

    # ── Validation ──────────────────────────────────────────────────────────

    def test_query_too_short_returns_400(self):
        response = self.client.get(BASE_URL, {'q': 'a'}, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json()['error']['code'], 'invalid_query')

    def test_missing_query_returns_400(self):
        self.assertEqual(self.client.get(BASE_URL, **self.slug).status_code, 400)

    # ── Type filtering ────────────────────────────────────────────────────────

    def test_types_filter_restricts_groups(self):
        self._seed()
        body = self.client.get(BASE_URL, {'q': 'mango', 'types': 'notes,tasks'}, **self.slug).json()
        self.assertEqual({g['type'] for g in body['groups']}, {'notes', 'tasks'})

    # ── Date filtering ──────────────────────────────────────────────────────

    def test_date_to_filters_out_recent(self):
        Note.objects.create(tenant=self.tenant, user=self.user, title='mango old', content='x')
        yesterday = (timezone.now() - timedelta(days=1)).date().isoformat()
        body = self.client.get(BASE_URL, {'q': 'mango', 'date_to': yesterday}, **self.slug).json()
        self.assertEqual(body['total'], 0)

    # ── Cross-tenant isolation ────────────────────────────────────────────────

    def test_cross_tenant_isolation(self):
        other = _create_tenant('other-corp')
        other_user = _create_superuser(other, 'o@other.com')
        Note.objects.create(tenant=other, user=other_user, title='mango secret', content='x')
        body = self.client.get(BASE_URL, {'q': 'mango'}, **self.slug).json()
        self.assertEqual(body['total'], 0)

    # ── Chat membership isolation ──────────────────────────────────────────────

    def test_chat_only_member_conversations(self):
        # Conversation the user belongs to.
        mine = Conversation.objects.create(tenant=self.tenant, type='group', name='Mine')
        ConversationMember.objects.create(conversation=mine, user=self.user, role='owner')
        Message.objects.create(conversation=mine, sender=self.user, content='hello mango')

        # Conversation the user is NOT a member of.
        stranger = _create_superuser(self.tenant, 'stranger@search.com')
        theirs = Conversation.objects.create(tenant=self.tenant, type='group', name='Theirs')
        ConversationMember.objects.create(conversation=theirs, user=stranger, role='owner')
        Message.objects.create(conversation=theirs, sender=stranger, content='secret mango')

        body = self.client.get(BASE_URL, {'q': 'mango', 'types': 'messages'}, **self.slug).json()
        self.assertEqual(body['total'], 1)
        self.assertIn('hello mango', body['groups'][0]['results'][0]['snippet'])

    def test_chat_excludes_deleted_messages(self):
        conv = Conversation.objects.create(tenant=self.tenant, type='group', name='C')
        ConversationMember.objects.create(conversation=conv, user=self.user, role='owner')
        Message.objects.create(
            conversation=conv, sender=self.user, content='deleted mango', deleted_at=timezone.now()
        )
        body = self.client.get(BASE_URL, {'q': 'mango', 'types': 'messages'}, **self.slug).json()
        self.assertEqual(body['total'], 0)
