"""
Tests for PASO 11 — AuditLog Module (Endpoints + Retención).
Covers: list, filters, retention window, pagination, plan gates,
        detail, cross-tenant isolation, purge task, AuditMixin.
"""
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.audit.models import AuditLog
from apps.audit.tasks import purge_old_audit_logs
from apps.tenants.models import Tenant
from core.mixins import AuditMixin

User = get_user_model()

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

BASE_URL = '/api/v1/admin/audit-logs/'


def _create_tenant(slug, plan='professional'):
    return Tenant.objects.create(name=slug.capitalize(), slug=slug, subdomain=slug, plan=plan)


def _create_superuser(tenant, email):
    user = User.objects.create_user(
        email=email, name='Test User', password='x', tenant=tenant
    )
    user.is_superuser = True
    user.save(update_fields=['is_superuser'])
    return user


def _create_log(tenant, user=None, action='test.action', resource_type='Resource',
                resource_id='', created_at=None):
    log = AuditLog.objects.create(
        tenant=tenant,
        user=user,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        ip_address='127.0.0.1',
        user_agent='TestAgent/1.0',
        extra={},
    )
    if created_at is not None:
        AuditLog.objects.filter(pk=log.pk).update(created_at=created_at)
        log.refresh_from_db()
    return log


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestAuditLogViews(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = _create_tenant('corp', plan='professional')
        self.owner = _create_superuser(self.tenant, 'owner@corp.com')
        self.client.force_authenticate(user=self.owner)
        self.slug = {'HTTP_X_TENANT_SLUG': 'corp'}

    # ─── List ─────────────────────────────────────────────────────────────────

    def test_list_audit_logs_empty(self):
        response = self.client.get(BASE_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['logs'], [])
        pagination = response.data['pagination']
        self.assertEqual(pagination['page'], 1)
        self.assertEqual(pagination['per_page'], 50)
        self.assertEqual(pagination['total'], 0)

    def test_list_audit_logs_returns_tenant_logs_only(self):
        other_tenant = _create_tenant('other')
        _create_log(other_tenant, action='foreign.action')
        _create_log(self.tenant, self.owner, action='own.action')

        response = self.client.get(BASE_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['logs']), 1)
        self.assertEqual(response.data['logs'][0]['action'], 'own.action')

    def test_list_filter_by_action(self):
        _create_log(self.tenant, self.owner, action='credentials.reveal')
        _create_log(self.tenant, self.owner, action='project.create')

        response = self.client.get(BASE_URL + '?action=credentials.reveal', **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['logs']), 1)
        self.assertEqual(response.data['logs'][0]['action'], 'credentials.reveal')

    def test_list_filter_by_user_id(self):
        other = User.objects.create_user(
            email='alice@corp.com', name='Alice', password='x', tenant=self.tenant
        )
        _create_log(self.tenant, self.owner, action='action.owner')
        _create_log(self.tenant, other, action='action.alice')

        response = self.client.get(BASE_URL + f'?user_id={other.pk}', **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['logs']), 1)
        self.assertEqual(response.data['logs'][0]['action'], 'action.alice')

    def test_list_filter_by_resource_type(self):
        _create_log(self.tenant, self.owner, resource_type='ProjectItemField')
        _create_log(self.tenant, self.owner, resource_type='Project')

        response = self.client.get(BASE_URL + '?resource_type=ProjectItemField', **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['logs']), 1)
        self.assertEqual(response.data['logs'][0]['resource_type'], 'ProjectItemField')

    def test_list_filter_by_date_range(self):
        old_date = timezone.now() - timedelta(days=10)
        _create_log(self.tenant, self.owner, action='old.action', created_at=old_date)
        _create_log(self.tenant, self.owner, action='new.action')

        date_from = (timezone.now() - timedelta(days=2)).date().isoformat()
        response = self.client.get(BASE_URL + f'?date_from={date_from}', **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['logs']), 1)
        self.assertEqual(response.data['logs'][0]['action'], 'new.action')

    def test_list_respects_retention_window(self):
        # Professional plan = 365 days retention
        old_date = timezone.now() - timedelta(days=400)
        _create_log(self.tenant, self.owner, action='old.outside', created_at=old_date)
        _create_log(self.tenant, self.owner, action='recent.inside')

        response = self.client.get(BASE_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        actions = [log['action'] for log in response.data['logs']]
        self.assertNotIn('old.outside', actions)
        self.assertIn('recent.inside', actions)

    def test_list_pagination_default(self):
        # Create 3 logs, check defaults
        for i in range(3):
            _create_log(self.tenant, self.owner, action=f'action.{i}')

        response = self.client.get(BASE_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['pagination']['page'], 1)
        self.assertEqual(response.data['pagination']['per_page'], 50)
        self.assertEqual(response.data['pagination']['total'], 3)

    def test_list_free_plan_blocked(self):
        free_tenant = _create_tenant('free-corp', plan='free')
        free_user = _create_superuser(free_tenant, 'admin@free.com')
        self.client.force_authenticate(user=free_user)

        response = self.client.get(BASE_URL, HTTP_X_TENANT_SLUG='free-corp')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_list_starter_plan_blocked(self):
        starter_tenant = _create_tenant('starter-corp', plan='starter')
        starter_user = _create_superuser(starter_tenant, 'admin@starter.com')
        self.client.force_authenticate(user=starter_user)

        response = self.client.get(BASE_URL, HTTP_X_TENANT_SLUG='starter-corp')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # ─── Detail ───────────────────────────────────────────────────────────────

    def test_detail_success(self):
        log = _create_log(self.tenant, self.owner, action='detail.test',
                          resource_type='Project', resource_id='abc123')

        response = self.client.get(f'{BASE_URL}{log.pk}/', **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['action'], 'detail.test')
        self.assertEqual(response.data['resource_type'], 'Project')
        self.assertEqual(response.data['user_email'], 'owner@corp.com')

    def test_detail_cross_tenant_blocked(self):
        other_tenant = _create_tenant('other2')
        log = _create_log(other_tenant, action='foreign.detail')

        response = self.client.get(f'{BASE_URL}{log.pk}/', **self.slug)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # ─── Purge Task ───────────────────────────────────────────────────────────

    def test_purge_task_deletes_old_logs(self):
        # Professional plan = 365 days; create log older than 365 days
        old_date = timezone.now() - timedelta(days=400)
        log = _create_log(self.tenant, self.owner, action='old.to.purge', created_at=old_date)

        result = purge_old_audit_logs()
        self.assertFalse(AuditLog.objects.filter(pk=log.pk).exists())
        self.assertIn(str(self.tenant.id), result)

    def test_purge_task_preserves_recent_logs(self):
        recent_log = _create_log(self.tenant, self.owner, action='recent.keep')

        result = purge_old_audit_logs()
        self.assertTrue(AuditLog.objects.filter(pk=recent_log.pk).exists())
        # No deletions for this tenant
        self.assertNotIn(str(self.tenant.id), result)

    # ─── AuditMixin ───────────────────────────────────────────────────────────

    def test_audit_mixin_log_action_creates_entry(self):
        from unittest.mock import MagicMock

        mixin = AuditMixin()
        request = MagicMock()
        request.tenant = self.tenant
        request.user = self.owner  # is_authenticated is True for real users
        request.META = {
            'REMOTE_ADDR': '10.0.0.1',
            'HTTP_USER_AGENT': 'TestBrowser/1.0',
        }

        count_before = AuditLog.objects.filter(tenant=self.tenant).count()
        mixin.log_action(request, 'mixin.test', 'Resource', resource_id='999',
                         extra={'key': 'value'})
        count_after = AuditLog.objects.filter(tenant=self.tenant).count()

        self.assertEqual(count_after, count_before + 1)
        log = AuditLog.objects.filter(tenant=self.tenant, action='mixin.test').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.ip_address, '10.0.0.1')
        self.assertEqual(log.user_agent, 'TestBrowser/1.0')
        self.assertEqual(log.extra, {'key': 'value'})
