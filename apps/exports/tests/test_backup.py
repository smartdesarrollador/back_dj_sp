"""
Tests for workspace backup export (/api/v1/app/workspace/backup/).

Covers: feature gating by plan, ZIP structure, secret masking, tenant isolation,
and audit logging.
"""
import io
import json
import zipfile

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.audit.models import AuditLog
from apps.notes.models import Note
from apps.projects.models import (
    Project,
    ProjectItem,
    ProjectItemField,
    ProjectSection,
)
from apps.tenants.models import Tenant

User = get_user_model()

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

BACKUP_URL = '/api/v1/app/workspace/backup/'


def _create_tenant(slug, plan='professional'):
    return Tenant.objects.create(name=slug.capitalize(), slug=slug, subdomain=slug, plan=plan)


def _create_superuser(tenant, email):
    user = User.objects.create_user(email=email, name='Test User', password='x', tenant=tenant)
    user.is_superuser = True
    user.save(update_fields=['is_superuser'])
    return user


def _read_zip(content: bytes) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        return {name: zf.read(name).decode('utf-8') for name in zf.namelist()}


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestWorkspaceBackup(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = _create_tenant('backup-corp', plan='professional')
        self.user = _create_superuser(self.tenant, 'u@backup.com')
        self.client.force_authenticate(user=self.user)
        self.slug = {'HTTP_X_TENANT_SLUG': 'backup-corp'}

    def test_backup_returns_zip_for_pro_plan(self):
        Note.objects.create(tenant=self.tenant, user=self.user, title='N1', content='hello')
        response = self.client.get(BACKUP_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response['Content-Type'], 'application/zip')

        files = _read_zip(response.content)
        for expected in ('notes.json', 'tasks.json', 'snippets.json', 'contacts.json',
                         'bookmarks.json', 'calendar.json', 'projects.json', 'manifest.json'):
            self.assertIn(expected, files)

        notes = json.loads(files['notes.json'])
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]['title'], 'N1')

    def test_backup_blocked_on_free_plan(self):
        self.tenant.plan = 'free'
        self.tenant.save(update_fields=['plan'])
        response = self.client.get(BACKUP_URL, **self.slug)
        self.assertNotEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn(response.status_code, (status.HTTP_402_PAYMENT_REQUIRED, status.HTTP_403_FORBIDDEN))

    def test_secrets_are_masked(self):
        project = Project.objects.create(tenant=self.tenant, created_by=self.user, name='P')
        section = ProjectSection.objects.create(project=project, name='S')
        item = ProjectItem.objects.create(section=section, name='Login')
        ProjectItemField.objects.create(
            item=item, label='Password', value='super-secret-123', field_type='password'
        )

        response = self.client.get(BACKUP_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        raw = _read_zip(response.content)['projects.json']
        self.assertNotIn('super-secret-123', raw)
        self.assertIn('***ENCRYPTED***', raw)

    def test_tenant_isolation(self):
        other_tenant = _create_tenant('other-corp', plan='professional')
        other_user = _create_superuser(other_tenant, 'o@other.com')
        Note.objects.create(tenant=other_tenant, user=other_user, title='LEAK', content='x')

        response = self.client.get(BACKUP_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        notes = json.loads(_read_zip(response.content)['notes.json'])
        self.assertEqual(notes, [])

    def test_backup_is_audited(self):
        self.client.get(BACKUP_URL, **self.slug)
        log = AuditLog.objects.filter(action='data.export', resource_type='workspace_backup').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.tenant_id, self.tenant.id)
