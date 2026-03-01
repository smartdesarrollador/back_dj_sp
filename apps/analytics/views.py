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
    from apps.audit.models import AuditLog
    from apps.bookmarks.models import Bookmark
    from apps.contacts.models import Contact
    from apps.forms_app.models import Form
    from apps.notes.models import Note
    from apps.projects.models import Project
    from apps.snippets.models import CodeSnippet

    start = timezone.now() - timedelta(days=period_days)
    return {
        'period_days': period_days,
        'active_users': User.objects.filter(tenant=tenant, last_login__gte=start).count(),
        'total_users': User.objects.filter(tenant=tenant).count(),
        'total_projects': Project.objects.filter(tenant=tenant).count(),
        'total_notes': Note.objects.filter(tenant=tenant).count(),
        'total_contacts': Contact.objects.filter(tenant=tenant).count(),
        'total_bookmarks': Bookmark.objects.filter(tenant=tenant).count(),
        'total_snippets': CodeSnippet.objects.filter(tenant=tenant).count(),
        'total_forms': Form.objects.filter(tenant=tenant).count(),
        'audit_events_period': AuditLog.objects.filter(
            tenant=tenant, created_at__gte=start
        ).count(),
    }


def _compute_usage(tenant) -> dict:
    from apps.bookmarks.models import Bookmark
    from apps.contacts.models import Contact
    from apps.forms_app.models import Form
    from apps.notes.models import Note
    from apps.snippets.models import CodeSnippet

    plan = tenant.plan
    resources_config = [
        ('forms', Form.objects.filter(tenant=tenant).count()),
        ('notes', Note.objects.filter(tenant=tenant).count()),
        ('contacts', Contact.objects.filter(tenant=tenant).count()),
        ('bookmarks', Bookmark.objects.filter(tenant=tenant).count()),
        ('snippets', CodeSnippet.objects.filter(tenant=tenant).count()),
        ('users', User.objects.filter(tenant=tenant).count()),
    ]
    resources = []
    for name, used in resources_config:
        limit = get_plan_limit(plan, name)
        percent = round(used / limit * 100, 1) if limit else None
        resources.append({'name': name, 'used': used, 'limit': limit, 'percent': percent})
    return {'plan': plan, 'resources': resources}


def _compute_trends(tenant) -> list:
    from apps.audit.models import AuditLog
    from django.db.models import Count
    from django.db.models.functions import TruncDate

    start = timezone.now() - timedelta(days=30)
    qs = (
        AuditLog.objects.filter(tenant=tenant, created_at__gte=start)
        .annotate(date=TruncDate('created_at'))
        .values('date')
        .annotate(events=Count('id'))
        .order_by('date')
    )
    return [{'date': row['date'], 'events': row['events']} for row in qs]


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

    @extend_schema(tags=['reports'], summary='Get daily audit event trends (last 30 days)')
    def get(self, request):
        cache_key = f'reports:trends:{request.tenant.pk}'
        trends = cache.get(cache_key)
        if trends is None:
            trends = _compute_trends(request.tenant)
            cache.set(cache_key, trends, 300)
        return Response({'trends': trends})


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
