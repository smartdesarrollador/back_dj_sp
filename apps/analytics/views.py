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

    start = timezone.now() - timedelta(days=period_days)
    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)

    tasks_qs = Task.objects.filter(tenant=tenant)
    active_tasks = tasks_qs.exclude(status='done').count()
    completed_tasks = tasks_qs.filter(status='done').count()
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
        'total_projects': total_projects,
        'storage_used_gb': 0,
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

    by_status = list(
        tasks_qs.values('status').annotate(count=Count('id')).order_by('status')
    )
    by_priority = list(
        tasks_qs.values('priority').annotate(count=Count('id')).order_by('priority')
    )

    return {
        'tasks_by_status': by_status,
        'tasks_by_priority': by_priority,
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
            'resource_usage': usage['resources'],
            'generated_at': timezone.now().isoformat(),
        }
        return Response({'report': report})
