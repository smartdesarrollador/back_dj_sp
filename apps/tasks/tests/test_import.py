"""Tests for bulk task import (POST /api/v1/app/tasks/import/)."""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.audit.models import AuditLog
from apps.tasks.models import Task, TaskBoard
from apps.tenants.models import Tenant

User = get_user_model()

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

IMPORT_URL = '/api/v1/app/tasks/import/'


def _create_tenant(slug, plan='professional'):
    return Tenant.objects.create(name=slug.capitalize(), slug=slug, subdomain=slug, plan=plan)


def _create_superuser(tenant, email):
    user = User.objects.create_user(email=email, name='T', password='x', tenant=tenant)
    user.is_superuser = True
    user.save(update_fields=['is_superuser'])
    return user


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestTaskImport(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = _create_tenant('tasks-imp', plan='professional')
        self.user = _create_superuser(self.tenant, 'u@tasks-imp.com')
        self.client.force_authenticate(user=self.user)
        self.slug = {'HTTP_X_TENANT_SLUG': 'tasks-imp'}

    def test_import_creates_tasks_in_single_general_board(self):
        items = [
            {'title': 'T1', 'status': 'todo', 'priority': 'high'},
            {'title': 'T2'},
            {'title': 'T3', 'due_date': '2026-07-01'},
        ]
        r = self.client.post(IMPORT_URL, {'items': items}, format='json', **self.slug)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.json()['created'], 3)
        boards = TaskBoard.objects.filter(tenant=self.tenant)
        self.assertEqual(boards.count(), 1)
        self.assertEqual(boards.first().name, 'General')
        self.assertEqual(Task.objects.filter(tenant=self.tenant, board=boards.first()).count(), 3)

    def test_feature_gate_blocks_free_plan(self):
        self.tenant.plan = 'free'
        self.tenant.save(update_fields=['plan'])
        r = self.client.post(IMPORT_URL, {'items': [{'title': 'X'}]}, format='json', **self.slug)
        self.assertIn(r.status_code, (status.HTTP_402_PAYMENT_REQUIRED, status.HTTP_403_FORBIDDEN))

    def test_invalid_rows_reported(self):
        items = [{'title': 'ok'}, {'description': 'no title'}]
        body = self.client.post(IMPORT_URL, {'items': items}, format='json', **self.slug).json()
        self.assertEqual(body['created'], 1)
        self.assertEqual(body['errors'][0]['index'], 1)

    def test_partial_plan_limit(self):
        self.tenant.plan = 'starter'  # max_tasks = 500
        self.tenant.save(update_fields=['plan'])
        items = [{'title': f'T{i}'} for i in range(501)]
        body = self.client.post(IMPORT_URL, {'items': items}, format='json', **self.slug).json()
        self.assertEqual(body['created'], 500)
        self.assertEqual(body['skipped'], 1)

    def test_row_cap(self):
        items = [{'title': 'x'} for _ in range(1001)]
        r = self.client.post(IMPORT_URL, {'items': items}, format='json', **self.slug)
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_import_is_audited(self):
        self.client.post(IMPORT_URL, {'items': [{'title': 'X'}]}, format='json', **self.slug)
        self.assertTrue(AuditLog.objects.filter(action='tasks.import', resource_type='Task').exists())
