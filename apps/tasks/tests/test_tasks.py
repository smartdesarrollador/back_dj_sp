"""
Tests for PASO 16 — Tasks module.
Covers: list boards, create task, plan limit, reorder, cross-tenant isolation.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.tasks.models import Task, TaskBoard
from apps.tenants.models import Tenant

User = get_user_model()

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

BOARDS_URL = '/api/v1/app/tasks/boards/'
TASKS_URL = '/api/v1/app/tasks/'
REORDER_URL = '/api/v1/app/tasks/reorder/'


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
class TestTaskViews(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = _create_tenant('tasks-corp')
        self.user = _create_superuser(self.tenant, 'u@tasks.com')
        self.client.force_authenticate(user=self.user)
        self.slug = {'HTTP_X_TENANT_SLUG': 'tasks-corp'}
        self.board = TaskBoard.objects.create(
            tenant=self.tenant,
            created_by=self.user,
            name='Sprint Board',
        )

    # ── List boards ───────────────────────────────────────────────────────────

    def test_list_boards_empty(self):
        """GET /tasks/boards/ returns list (may include setUp board)."""
        response = self.client.get(BOARDS_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertIn('boards', body)
        self.assertIsInstance(body['boards'], list)

    # ── Create task ───────────────────────────────────────────────────────────

    def test_create_task_success(self):
        """POST /tasks/ creates task in the correct board."""
        data = {
            'board': str(self.board.pk),
            'title': 'Fix login bug',
            'priority': 'high',
            'status': 'todo',
        }
        response = self.client.post(TASKS_URL, data, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        body = response.json()
        self.assertEqual(body['title'], 'Fix login bug')
        self.assertEqual(body['priority'], 'high')
        self.assertTrue(Task.objects.filter(tenant=self.tenant, title='Fix login bug').exists())

    # ── Plan limit ────────────────────────────────────────────────────────────

    def test_create_task_exceeds_plan_limit(self):
        """POST /tasks/ raises 402 when plan limit is exceeded."""
        with patch('apps.tasks.views.check_plan_limit') as mock_limit:
            from core.exceptions import PlanLimitExceeded
            mock_limit.side_effect = PlanLimitExceeded()
            data = {'board': str(self.board.pk), 'title': 'Over limit'}
            response = self.client.post(TASKS_URL, data, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)

    # ── Reorder ───────────────────────────────────────────────────────────────

    def test_task_reorder(self):
        """PATCH /tasks/reorder/ updates task order in bulk."""
        task1 = Task.objects.create(
            tenant=self.tenant, board=self.board,
            created_by=self.user, title='Task A', order=0,
        )
        task2 = Task.objects.create(
            tenant=self.tenant, board=self.board,
            created_by=self.user, title='Task B', order=1,
        )
        payload = [
            {'id': str(task1.pk), 'order': 5},
            {'id': str(task2.pk), 'order': 2},
        ]
        response = self.client.patch(
            REORDER_URL, payload, format='json', **self.slug
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        task1.refresh_from_db()
        task2.refresh_from_db()
        self.assertEqual(task1.order, 5)
        self.assertEqual(task2.order, 2)

    # ── Cross-tenant isolation ────────────────────────────────────────────────

    def test_cross_tenant_task_isolated(self):
        """User from another tenant cannot see tasks from a different tenant."""
        other_tenant = _create_tenant('other-tasks')
        other_user = _create_superuser(other_tenant, 'other@tasks.com')
        other_board = TaskBoard.objects.create(
            tenant=other_tenant, created_by=other_user, name='Other Board'
        )
        other_task = Task.objects.create(
            tenant=other_tenant, board=other_board,
            created_by=other_user, title='Secret task',
        )
        url = f'{TASKS_URL}{other_task.pk}/'
        response = self.client.get(url, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
