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

    # ── List tasks (pagination) ──────────────────────────────────────────────

    def _create_tasks(self, n, **overrides):
        tasks = []
        for i in range(n):
            defaults = {
                'tenant': self.tenant,
                'board': self.board,
                'created_by': self.user,
                'title': f'Task {i}',
                'order': i,
            }
            defaults.update(overrides)
            tasks.append(Task.objects.create(**defaults))
        return tasks

    def test_list_tasks_without_page_returns_everything(self):
        """GET /tasks/ without ?page= keeps today's behavior: no slicing."""
        self._create_tasks(25)
        response = self.client.get(TASKS_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(len(body['tasks']), 25)
        self.assertEqual(body['pagination'], {'page': 1, 'per_page': 25, 'total': 25})

    def test_list_tasks_first_page_default_per_page(self):
        """?page=1 without per_page defaults to 20 items per page."""
        self._create_tasks(25)
        response = self.client.get(TASKS_URL, {'page': 1}, **self.slug)
        body = response.json()
        self.assertEqual(len(body['tasks']), 20)
        self.assertEqual(body['pagination'], {'page': 1, 'per_page': 20, 'total': 25})

    def test_list_tasks_second_page(self):
        """?page=2 returns the remaining items."""
        self._create_tasks(25)
        response = self.client.get(TASKS_URL, {'page': 2}, **self.slug)
        body = response.json()
        self.assertEqual(len(body['tasks']), 5)
        self.assertEqual(body['pagination'], {'page': 2, 'per_page': 20, 'total': 25})

    def test_list_tasks_custom_per_page(self):
        """?per_page= overrides the default page size."""
        self._create_tasks(25)
        response = self.client.get(TASKS_URL, {'page': 1, 'per_page': 5}, **self.slug)
        body = response.json()
        self.assertEqual(len(body['tasks']), 5)
        self.assertEqual(body['pagination']['per_page'], 5)

    def test_list_tasks_per_page_clamped_to_100(self):
        """per_page above 100 is clamped, doesn't 500 or leak unbounded rows."""
        self._create_tasks(3)
        response = self.client.get(TASKS_URL, {'page': 1, 'per_page': 500}, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['pagination']['per_page'], 100)

    def test_list_tasks_page_out_of_range_returns_empty(self):
        """A page beyond the last one returns 200 with an empty list, not 404."""
        self._create_tasks(3)
        response = self.client.get(TASKS_URL, {'page': 999}, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body['tasks'], [])
        self.assertEqual(body['pagination']['total'], 3)

    def test_list_tasks_invalid_page_falls_back_to_default(self):
        """A non-numeric page falls back to page=1 instead of 500ing."""
        self._create_tasks(3)
        response = self.client.get(TASKS_URL, {'page': 'abc'}, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['pagination']['page'], 1)

    def test_list_tasks_invalid_per_page_falls_back_to_default(self):
        """A non-numeric per_page falls back to 20 instead of 500ing."""
        self._create_tasks(3)
        response = self.client.get(TASKS_URL, {'page': 1, 'per_page': 'xyz'}, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['pagination']['per_page'], 20)

    def test_list_tasks_negative_page_clamped_to_one(self):
        """A negative page is clamped to 1."""
        self._create_tasks(3)
        response = self.client.get(TASKS_URL, {'page': -5}, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['pagination']['page'], 1)

    def test_list_tasks_filters_combined_with_pagination(self):
        """total reflects only the filtered subset, not the whole tenant."""
        self._create_tasks(3, status='todo')
        self._create_tasks(4, status='done')
        response = self.client.get(
            TASKS_URL, {'status': 'todo', 'page': 1, 'per_page': 2}, **self.slug
        )
        body = response.json()
        self.assertEqual(len(body['tasks']), 2)
        self.assertEqual(body['pagination']['total'], 3)
        self.assertTrue(all(t['status'] == 'todo' for t in body['tasks']))

    def test_list_tasks_cross_tenant_pagination_isolated(self):
        """total/pagination never counts another tenant's tasks."""
        other_tenant = _create_tenant('other-pagination')
        other_user = _create_superuser(other_tenant, 'other@pagination.com')
        other_board = TaskBoard.objects.create(
            tenant=other_tenant, created_by=other_user, name='Other Board'
        )
        Task.objects.create(
            tenant=other_tenant, board=other_board,
            created_by=other_user, title='Other tenant task',
        )
        self._create_tasks(2)
        response = self.client.get(TASKS_URL, {'page': 1}, **self.slug)
        self.assertEqual(response.json()['pagination']['total'], 2)
