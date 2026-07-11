"""
Analytics/Reports views — computed metrics dashboard for the tenant.

URL namespace: /api/v1/app/reports/

Endpoints:
  GET  /app/reports/summary/  → tenant metrics summary
  GET  /app/reports/usage/    → resource usage vs plan limits
  GET  /app/reports/trends/   → daily audit events for last 30 days
  GET  /app/reports/export/   → executive report JSON export
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from drf_spectacular.types import OpenApiTypes
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.rbac.permissions import HasFeature, HasPermission
from utils.plans import PLAN_FEATURES, get_plan_limit

User = get_user_model()


def _compute_summary(tenant, period_days: int) -> dict:
    from apps.bookmarks.models import Bookmark
    from apps.calendar_app.models import CalendarEvent
    from apps.contacts.models import Contact
    from apps.notes.models import Note
    from apps.projects.models import Project
    from apps.snippets.models import CodeSnippet
    from apps.tasks.models import Task
    from utils.storage import get_tenant_storage_bytes

    start = timezone.now() - timedelta(days=period_days)
    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    today = timezone.now().date()

    tasks_qs = Task.objects.filter(tenant=tenant)
    active_tasks = tasks_qs.exclude(status='done').count()
    completed_tasks = tasks_qs.filter(status='done').count()
    overdue_tasks = tasks_qs.filter(due_date__lt=today).exclude(status='done').count()
    total_projects = Project.objects.filter(tenant=tenant, is_archived=False).count()
    total_notes = Note.objects.filter(tenant=tenant).count()
    total_contacts = Contact.objects.filter(tenant=tenant).count()
    total_bookmarks = Bookmark.objects.filter(tenant=tenant).count()
    total_snippets = CodeSnippet.objects.filter(tenant=tenant).count()
    events_today = CalendarEvent.objects.filter(
        tenant=tenant,
        start_datetime__gte=today_start,
        start_datetime__lt=today_end,
    ).count()

    plan = tenant.plan
    return {
        'period_days': period_days,
        # ReportsPage KPI cards
        'active_tasks': active_tasks,
        'completed_tasks': completed_tasks,
        'overdue_tasks': overdue_tasks,
        'total_projects': total_projects,
        'storage_used_gb': round(get_tenant_storage_bytes(tenant) / 1024 ** 3, 3),
        # DashboardPage fields
        'total_notes': total_notes,
        'events_today': events_today,
        # Per-feature usage vs plan limits (DashboardSummary.usage)
        'usage': {
            'tasks_active': active_tasks,
            'tasks_limit': get_plan_limit(plan, 'tasks'),
            'projects': total_projects,
            'projects_limit': get_plan_limit(plan, 'projects'),
            'notes': total_notes,
            'notes_limit': get_plan_limit(plan, 'notes'),
            'contacts': total_contacts,
            'contacts_limit': get_plan_limit(plan, 'contacts'),
            'bookmarks': total_bookmarks,
            'bookmarks_limit': get_plan_limit(plan, 'bookmarks'),
            'snippets': total_snippets,
            'snippets_limit': get_plan_limit(plan, 'snippets'),
        },
    }


def _compute_usage(tenant) -> dict:
    from apps.tasks.models import Task
    from django.db.models import Count

    tasks_qs = Task.objects.filter(tenant=tenant)
    today = timezone.now().date()

    by_status = list(
        tasks_qs.values('status').annotate(count=Count('id')).order_by('status')
    )
    by_priority = list(
        tasks_qs.values('priority').annotate(count=Count('id')).order_by('priority')
    )
    overdue = list(
        tasks_qs.filter(due_date__lt=today).exclude(status='done')
        .order_by('due_date')
        .values('id', 'title', 'due_date', 'priority')[:5]
    )

    return {
        'tasks_by_status': by_status,
        'tasks_by_priority': by_priority,
        'overdue': overdue,
    }


def _compute_trends(tenant, period_days: int = 30) -> dict:
    from apps.projects.models import Project
    from apps.tasks.models import Task
    from django.db.models import Count
    from django.db.models.functions import TruncDate

    start = timezone.now() - timedelta(days=period_days)

    completed = (
        Task.objects.filter(tenant=tenant, status='done', updated_at__gte=start)
        .annotate(date=TruncDate('updated_at'))
        .values('date').annotate(count=Count('id')).order_by('date')
    )
    active = (
        Task.objects.filter(tenant=tenant, updated_at__gte=start).exclude(status='done')
        .annotate(date=TruncDate('updated_at'))
        .values('date').annotate(count=Count('id')).order_by('date')
    )
    new_projects = (
        Project.objects.filter(tenant=tenant, created_at__gte=start)
        .annotate(date=TruncDate('created_at'))
        .values('date').annotate(count=Count('id')).order_by('date')
    )

    # Merge into date-keyed dict
    merged: dict = {}
    for row in completed:
        d = str(row['date'])
        merged.setdefault(d, {'date': d, 'active_tasks': 0, 'completed_tasks': 0, 'new_projects': 0})
        merged[d]['completed_tasks'] = row['count']
    for row in active:
        d = str(row['date'])
        merged.setdefault(d, {'date': d, 'active_tasks': 0, 'completed_tasks': 0, 'new_projects': 0})
        merged[d]['active_tasks'] = row['count']
    for row in new_projects:
        d = str(row['date'])
        merged.setdefault(d, {'date': d, 'active_tasks': 0, 'completed_tasks': 0, 'new_projects': 0})
        merged[d]['new_projects'] = row['count']

    data = sorted(merged.values(), key=lambda x: x['date'])
    return {'period': f'{period_days}d', 'data': data}


_STALE_SECRET_DAYS = 90


def _compute_devops(tenant, user) -> dict:
    """DevOps report scoped to the requesting user (SSL/secrets/snippets are personal)."""
    from apps.env_vars.models import EnvVariable
    from apps.snippets.models import CodeSnippet
    from apps.ssh_keys.models import SSHKey
    from apps.ssl_certs.models import SSLCertificate
    from apps.vault.models import VaultItem
    from django.db.models import Count

    today = timezone.now().date()
    cutoff = timezone.now() - timedelta(days=_STALE_SECRET_DAYS)
    soon = today + timedelta(days=30)

    # ── SSL certificates ──────────────────────────────────────────────────────
    ssl_qs = SSLCertificate.objects.filter(tenant=tenant, user=user)
    expired = ssl_qs.filter(valid_until__lt=today).count()
    expiring = ssl_qs.filter(valid_until__gte=today, valid_until__lte=soon).count()
    valid = ssl_qs.count() - expired - expiring  # remainder incl. null valid_until
    expiring_soon = [
        {
            'id': str(cert.id),
            'domain': cert.domain,
            'valid_until': cert.valid_until.isoformat() if cert.valid_until else None,
            'days_until_expiry': cert.days_until_expiry,
        }
        for cert in ssl_qs.filter(valid_until__isnull=False, valid_until__lte=soon).order_by('valid_until')[:5]
    ]

    # ── Secrets hygiene (rotation) ────────────────────────────────────────────
    env_qs = EnvVariable.objects.filter(tenant=tenant, user=user)
    ssh_qs = SSHKey.objects.filter(tenant=tenant, user=user)
    vault_qs = VaultItem.objects.filter(tenant=tenant, user=user)

    stale = (
        env_qs.filter(updated_at__lt=cutoff).count()
        + ssh_qs.filter(updated_at__lt=cutoff).count()
        + vault_qs.filter(updated_at__lt=cutoff).count()
    )
    oldest_rows: list[dict] = []
    for label_field, type_name, qs in (
        ('key', 'env_var', env_qs),
        ('name', 'ssh_key', ssh_qs),
        ('title', 'vault_item', vault_qs),
    ):
        for row in qs.filter(updated_at__lt=cutoff).order_by('updated_at').values(label_field, 'updated_at')[:5]:
            oldest_rows.append({
                'type': type_name,
                'label': row[label_field],
                'updated_at': row['updated_at'].isoformat(),
            })
    oldest = sorted(oldest_rows, key=lambda r: r['updated_at'])[:5]

    # ── Snippets by language ──────────────────────────────────────────────────
    snippets_by_language = list(
        CodeSnippet.objects.filter(tenant=tenant, user=user)
        .values('language').annotate(count=Count('id')).order_by('-count')
    )

    return {
        'ssl': {
            'valid': valid,
            'expiring': expiring,
            'expired': expired,
            'expiring_soon': expiring_soon,
        },
        'secrets': {
            'env_vars': env_qs.count(),
            'ssh_keys': ssh_qs.count(),
            'vault_items': vault_qs.count(),
            'stale': stale,
            'stale_days': _STALE_SECRET_DAYS,
            'oldest': oldest,
        },
        'snippets_by_language': snippets_by_language,
    }


def _compute_activity(tenant, period_days: int) -> dict:
    """Aggregated AuditLog activity for the tenant (timeline + by action)."""
    from apps.audit.models import AuditLog
    from django.db.models import Count
    from django.db.models.functions import TruncDate

    retention = PLAN_FEATURES.get(tenant.plan, PLAN_FEATURES['free']).get('audit_log_days', 7)
    effective_days = min(period_days, retention)
    start = timezone.now() - timedelta(days=effective_days)

    qs = AuditLog.objects.filter(tenant=tenant, created_at__gte=start)

    by_day = [
        {'date': str(row['date']), 'count': row['count']}
        for row in (
            qs.annotate(date=TruncDate('created_at'))
            .values('date').annotate(count=Count('id')).order_by('date')
        )
    ]
    by_action = list(
        qs.values('action').annotate(count=Count('id')).order_by('-count')[:8]
    )

    return {
        'period': f'{effective_days}d',
        'requested_days': period_days,
        'retention_days': retention,
        'total': qs.count(),
        'by_day': by_day,
        'by_action': by_action,
    }


class SummaryView(APIView):
    permission_classes = [HasPermission('reports.read'), HasFeature('analytics')]

    @extend_schema(
        tags=['reports'],
        summary='Get tenant metrics summary',
        parameters=[
            OpenApiParameter('period', OpenApiTypes.INT, description='Period in days (default: 30, max: 365)'),
        ],
    )
    def get(self, request):
        try:
            period_days = min(int(request.query_params.get('period', 30)), 365)
        except (ValueError, TypeError):
            period_days = 30

        cache_key = f'reports:summary:{request.tenant.pk}:{period_days}'
        data = cache.get(cache_key)
        if data is None:
            data = _compute_summary(request.tenant, period_days)
            cache.set(cache_key, data, 300)
        return Response(data)


class UsageView(APIView):
    permission_classes = [HasPermission('reports.read'), HasFeature('analytics')]

    @extend_schema(tags=['reports'], summary='Get resource usage vs plan limits')
    def get(self, request):
        cache_key = f'reports:usage:{request.tenant.pk}'
        data = cache.get(cache_key)
        if data is None:
            data = _compute_usage(request.tenant)
            cache.set(cache_key, data, 300)
        return Response(data)


class DevOpsView(APIView):
    permission_classes = [HasPermission('reports.read'), HasFeature('analytics')]

    @extend_schema(tags=['reports'], summary='Get DevOps report (SSL, secrets, snippets)')
    def get(self, request):
        cache_key = f'reports:devops:{request.tenant.pk}:{request.user.pk}'
        data = cache.get(cache_key)
        if data is None:
            data = _compute_devops(request.tenant, request.user)
            cache.set(cache_key, data, 300)
        return Response(data)


class ActivityView(APIView):
    permission_classes = [HasPermission('reports.read'), HasFeature('audit_logs')]

    @extend_schema(
        tags=['reports'],
        summary='Get aggregated audit activity (timeline + by action)',
        parameters=[
            OpenApiParameter('period', OpenApiTypes.INT, description='Period in days (default: 30, max: 90)'),
        ],
    )
    def get(self, request):
        try:
            period_days = min(int(request.query_params.get('period', 30)), 90)
        except (ValueError, TypeError):
            period_days = 30
        cache_key = f'reports:activity:{request.tenant.pk}:{period_days}'
        data = cache.get(cache_key)
        if data is None:
            data = _compute_activity(request.tenant, period_days)
            cache.set(cache_key, data, 300)
        return Response(data)


class TrendsView(APIView):
    permission_classes = [HasPermission('reports.read'), HasFeature('analytics_trends')]

    @extend_schema(tags=['reports'], summary='Get daily task/project trends')
    def get(self, request):
        try:
            period_days = min(int(request.query_params.get('period', 30)), 90)
        except (ValueError, TypeError):
            period_days = 30
        cache_key = f'reports:trends:{request.tenant.pk}:{period_days}'
        data = cache.get(cache_key)
        if data is None:
            data = _compute_trends(request.tenant, period_days)
            cache.set(cache_key, data, 300)
        return Response(data)


class ReportExportView(APIView):
    permission_classes = [HasPermission('reports.read'), HasFeature('pdf_export')]

    @extend_schema(
        tags=['reports'],
        summary='Export executive report as JSON',
        parameters=[
            OpenApiParameter('period', OpenApiTypes.INT, description='Period in days (default: 30, max: 365)'),
        ],
    )
    def get(self, request):
        try:
            period_days = min(int(request.query_params.get('period', 30)), 365)
        except (ValueError, TypeError):
            period_days = 30
        summary = _compute_summary(request.tenant, period_days)
        usage = _compute_usage(request.tenant)
        report = {
            **summary,
            'plan': request.tenant.plan,
            'tasks_by_status': usage['tasks_by_status'],
            'tasks_by_priority': usage['tasks_by_priority'],
            'overdue': usage['overdue'],
            'generated_at': timezone.now().isoformat(),
        }
        return Response({'report': report})
