"""
Tests for PASO 14 — Analytics/Reports module.
Covers: feature gates (free/starter/professional), summary keys, usage breakdown, trends, export.
"""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.tenants.models import Tenant

User = get_user_model()

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

SUMMARY_URL = '/api/v1/app/reports/summary/'
USAGE_URL = '/api/v1/app/reports/usage/'
TRENDS_URL = '/api/v1/app/reports/trends/'
EXPORT_URL = '/api/v1/app/reports/export/'
DEVOPS_URL = '/api/v1/app/reports/devops/'
ACTIVITY_URL = '/api/v1/app/reports/activity/'


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
class TestReportViews(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = _create_tenant('analytics-corp', plan='professional')
        self.user = _create_superuser(self.tenant, 'u@analytics.com')
        self.client.force_authenticate(user=self.user)
        self.slug = {'HTTP_X_TENANT_SLUG': 'analytics-corp'}

    # ── Summary requires analytics feature ───────────────────────────────────

    def test_summary_requires_analytics_feature(self):
        free_tenant = _create_tenant('free-analytics', plan='free')
        free_user = _create_superuser(free_tenant, 'u@free-analytics.com')
        self.client.force_authenticate(user=free_user)
        response = self.client.get(SUMMARY_URL, **{'HTTP_X_TENANT_SLUG': 'free-analytics'})
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)

    # ── Summary returns expected metrics keys ─────────────────────────────────

    def test_summary_returns_metrics_keys(self):
        response = self.client.get(SUMMARY_URL + '?period=30', **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        # Task-oriented summary (powers the ReportsPage KPI cards)
        self.assertIn('active_tasks', body)
        self.assertIn('completed_tasks', body)
        self.assertIn('overdue_tasks', body)
        self.assertIn('period_days', body)
        self.assertEqual(body['period_days'], 30)

    # ── Usage returns the task breakdown ──────────────────────────────────────

    def test_usage_returns_task_breakdown(self):
        response = self.client.get(USAGE_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertIn('tasks_by_status', body)
        self.assertIn('tasks_by_priority', body)
        self.assertIn('overdue', body)
        self.assertIsInstance(body['tasks_by_status'], list)
        self.assertIsInstance(body['overdue'], list)

    # ── Trends requires analytics_trends feature ──────────────────────────────

    def test_trends_requires_analytics_trends_feature(self):
        starter_tenant = _create_tenant('starter-analytics', plan='starter')
        starter_user = _create_superuser(starter_tenant, 'u@starter-analytics.com')
        self.client.force_authenticate(user=starter_user)
        response = self.client.get(TRENDS_URL, **{'HTTP_X_TENANT_SLUG': 'starter-analytics'})
        # starter has analytics=True but analytics_trends=False → 402
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)

    # ── Report export requires pdf_export feature ─────────────────────────────

    def test_report_export_requires_pdf_export_feature(self):
        starter_tenant = _create_tenant('starter-export', plan='starter')
        starter_user = _create_superuser(starter_tenant, 'u@starter-export.com')
        self.client.force_authenticate(user=starter_user)
        response = self.client.get(EXPORT_URL, **{'HTTP_X_TENANT_SLUG': 'starter-export'})
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)

    # ── Report export works for a plan with pdf_export (regression: no KeyError) ─

    def test_report_export_returns_report_for_professional(self):
        response = self.client.get(EXPORT_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        report = response.json()['report']
        self.assertIn('tasks_by_status', report)
        self.assertIn('overdue', report)
        self.assertIn('generated_at', report)


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestOverdueTasks(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = _create_tenant('overdue-corp', plan='professional')
        self.user = _create_superuser(self.tenant, 'u@overdue.com')
        self.client.force_authenticate(user=self.user)
        self.slug = {'HTTP_X_TENANT_SLUG': 'overdue-corp'}

        from apps.tasks.models import TaskBoard
        self.board = TaskBoard.objects.create(
            tenant=self.tenant, name='General', created_by=self.user
        )

    def _task(self, title, due_date, status_='todo', priority='medium'):
        from apps.tasks.models import Task
        return Task.objects.create(
            tenant=self.tenant, board=self.board, created_by=self.user,
            title=title, status=status_, priority=priority, due_date=due_date,
        )

    def test_overdue_counts_only_past_uncompleted(self):
        from datetime import timedelta
        from django.utils import timezone
        yesterday = timezone.now().date() - timedelta(days=1)
        tomorrow = timezone.now().date() + timedelta(days=1)
        self._task('Vencida pendiente', yesterday, 'todo', 'high')
        self._task('Vencida pero hecha', yesterday, 'done', 'high')  # excluida
        self._task('Futura', tomorrow, 'todo')                        # excluida
        self._task('Sin fecha', None, 'todo')                         # excluida

        summary = self.client.get(SUMMARY_URL, **self.slug).json()
        self.assertEqual(summary['overdue_tasks'], 1)

        usage = self.client.get(USAGE_URL, **self.slug).json()
        self.assertEqual(len(usage['overdue']), 1)
        self.assertEqual(usage['overdue'][0]['title'], 'Vencida pendiente')

    def test_overdue_list_capped_at_five_ordered_by_date(self):
        from datetime import timedelta
        from django.utils import timezone
        today = timezone.now().date()
        for i in range(7):
            self._task(f'V{i}', today - timedelta(days=i + 1))

        usage = self.client.get(USAGE_URL, **self.slug).json()
        self.assertEqual(len(usage['overdue']), 5)
        dates = [row['due_date'] for row in usage['overdue']]
        self.assertEqual(dates, sorted(dates))  # ascending (most overdue first)


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestDevOpsReport(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = _create_tenant('devops-corp', plan='professional')
        self.user = _create_superuser(self.tenant, 'u@devops.com')
        self.other = _create_superuser(self.tenant, 'other@devops.com')
        self.client.force_authenticate(user=self.user)
        self.slug = {'HTTP_X_TENANT_SLUG': 'devops-corp'}

    def _cert(self, domain, valid_until, user=None):
        from apps.ssl_certs.models import SSLCertificate
        return SSLCertificate.objects.create(
            tenant=self.tenant, user=user or self.user, domain=domain, valid_until=valid_until,
        )

    def _age(self, model, pk, days):
        """Backdate updated_at (bypasses auto_now via .update())."""
        from datetime import timedelta
        from django.utils import timezone
        model.objects.filter(pk=pk).update(updated_at=timezone.now() - timedelta(days=days))

    def test_ssl_buckets_and_expiring_soon(self):
        from datetime import timedelta
        from django.utils import timezone
        today = timezone.now().date()
        self._cert('expired.com', today - timedelta(days=2))
        self._cert('soon.com', today + timedelta(days=10))
        self._cert('safe.com', today + timedelta(days=200))

        body = self.client.get(DEVOPS_URL, **self.slug).json()
        self.assertEqual(body['ssl']['expired'], 1)
        self.assertEqual(body['ssl']['expiring'], 1)
        self.assertEqual(body['ssl']['valid'], 1)
        # expiring_soon: expired + expiring (≤30d), ordered ascending by date
        domains = [c['domain'] for c in body['ssl']['expiring_soon']]
        self.assertEqual(domains, ['expired.com', 'soon.com'])

    def test_secrets_stale_and_oldest(self):
        from apps.env_vars.models import EnvVariable
        from apps.ssh_keys.models import SSHKey

        fresh = EnvVariable.objects.create(
            tenant=self.tenant, user=self.user, key='FRESH', value='v',
        )
        old_env = EnvVariable.objects.create(
            tenant=self.tenant, user=self.user, key='OLD_API_KEY', value='v',
        )
        old_ssh = SSHKey.objects.create(
            tenant=self.tenant, user=self.user, name='old-deploy-key',
            public_key='ssh-rsa AAAA deploy',
        )
        self._age(EnvVariable, old_env.pk, 120)
        self._age(SSHKey, old_ssh.pk, 200)

        body = self.client.get(DEVOPS_URL, **self.slug).json()
        secrets = body['secrets']
        self.assertEqual(secrets['env_vars'], 2)
        self.assertEqual(secrets['ssh_keys'], 1)
        self.assertEqual(secrets['stale'], 2)  # fresh excluded
        self.assertEqual(secrets['stale_days'], 90)
        labels = [o['label'] for o in secrets['oldest']]
        self.assertIn('old-deploy-key', labels)
        self.assertIn('OLD_API_KEY', labels)
        self.assertNotIn('FRESH', labels)

    def test_snippets_by_language(self):
        from apps.snippets.models import CodeSnippet
        CodeSnippet.objects.create(tenant=self.tenant, user=self.user, title='a', code='x', language='python')
        CodeSnippet.objects.create(tenant=self.tenant, user=self.user, title='b', code='x', language='python')
        CodeSnippet.objects.create(tenant=self.tenant, user=self.user, title='c', code='x', language='go')

        body = self.client.get(DEVOPS_URL, **self.slug).json()
        by_lang = {row['language']: row['count'] for row in body['snippets_by_language']}
        self.assertEqual(by_lang['python'], 2)
        self.assertEqual(by_lang['go'], 1)

    def test_scoped_to_requesting_user(self):
        from datetime import timedelta
        from django.utils import timezone
        # certs belonging to another user must not be counted
        self._cert('other-expired.com', timezone.now().date() - timedelta(days=1), user=self.other)

        body = self.client.get(DEVOPS_URL, **self.slug).json()
        self.assertEqual(body['ssl']['expired'], 0)
        self.assertEqual(body['ssl']['valid'], 0)

    def test_requires_analytics_feature(self):
        free_tenant = _create_tenant('free-devops', plan='free')
        free_user = _create_superuser(free_tenant, 'u@free-devops.com')
        self.client.force_authenticate(user=free_user)
        response = self.client.get(DEVOPS_URL, **{'HTTP_X_TENANT_SLUG': 'free-devops'})
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestActivityReport(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = _create_tenant('activity-corp', plan='professional')  # audit_logs=True
        self.user = _create_superuser(self.tenant, 'u@activity.com')
        self.client.force_authenticate(user=self.user)
        self.slug = {'HTTP_X_TENANT_SLUG': 'activity-corp'}

    def _log(self, action, days_ago=0):
        from datetime import timedelta
        from django.utils import timezone
        from apps.audit.models import AuditLog
        entry = AuditLog.objects.create(
            tenant=self.tenant, user=self.user, action=action, resource_type='Task',
        )
        if days_ago:
            # created_at is auto_now_add → backdate via .update()
            AuditLog.objects.filter(pk=entry.pk).update(
                created_at=timezone.now() - timedelta(days=days_ago)
            )
        return entry

    def test_activity_totals_and_by_action(self):
        self._log('tasks.import')
        self._log('tasks.import')
        self._log('notes.import')

        body = self.client.get(ACTIVITY_URL, **self.slug).json()
        self.assertEqual(body['total'], 3)
        by_action = {row['action']: row['count'] for row in body['by_action']}
        self.assertEqual(by_action['tasks.import'], 2)
        self.assertEqual(by_action['notes.import'], 1)
        # by_action ordered by count desc
        self.assertEqual(body['by_action'][0]['action'], 'tasks.import')

    def test_by_day_groups_dates_and_respects_period(self):
        self._log('a', days_ago=0)
        self._log('b', days_ago=1)
        self._log('c', days_ago=200)  # outside a 30-day window

        body = self.client.get(ACTIVITY_URL + '?period=30', **self.slug).json()
        self.assertEqual(body['total'], 2)
        self.assertEqual(len(body['by_day']), 2)
        dates = [row['date'] for row in body['by_day']]
        self.assertEqual(dates, sorted(dates))

    def test_requires_audit_logs_feature(self):
        # starter has analytics but NOT audit_logs → 402
        starter_tenant = _create_tenant('starter-activity', plan='starter')
        starter_user = _create_superuser(starter_tenant, 'u@starter-activity.com')
        self.client.force_authenticate(user=starter_user)
        response = self.client.get(ACTIVITY_URL, **{'HTTP_X_TENANT_SLUG': 'starter-activity'})
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)
