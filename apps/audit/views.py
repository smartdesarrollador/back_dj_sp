"""
AuditLog views — endpoints de solo lectura con filtros y retención por plan.
"""
from datetime import timedelta

from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from drf_spectacular.types import OpenApiTypes
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.models import AuditLog
from apps.audit.serializers import AuditLogSerializer
from apps.rbac.permissions import HasFeature, HasPermission
from utils.plans import PLAN_FEATURES

_NOT_FOUND = {'error': {'code': 'not_found', 'message': 'Audit log not found.'}}


def _apply_retention(qs, tenant):
    """Filtra el queryset según la ventana de retención del plan del tenant."""
    retention_days = PLAN_FEATURES.get(tenant.plan, PLAN_FEATURES['free']).get('audit_log_days', 7)
    cutoff = timezone.now() - timedelta(days=retention_days)
    return qs.filter(created_at__gte=cutoff)


class AuditLogListView(APIView):
    permission_classes = [HasFeature('audit_logs'), HasPermission('audit.read')]

    @extend_schema(
        tags=['audit'],
        summary='List audit logs',
        parameters=[
            OpenApiParameter('action', OpenApiTypes.STR, description='Filter by action'),
            OpenApiParameter('user_id', OpenApiTypes.UUID, description='Filter by user'),
            OpenApiParameter('resource_type', OpenApiTypes.STR, description='Filter by resource type'),
            OpenApiParameter('resource_id', OpenApiTypes.UUID, description='Filter by resource ID'),
            OpenApiParameter('date_from', OpenApiTypes.DATE, description='Filter from date (YYYY-MM-DD)'),
            OpenApiParameter('date_to', OpenApiTypes.DATE, description='Filter to date (YYYY-MM-DD)'),
            OpenApiParameter('page', OpenApiTypes.INT, description='Page number (default: 1)'),
            OpenApiParameter('per_page', OpenApiTypes.INT, description='Results per page (max: 100)'),
        ],
    )
    def get(self, request):
        qs = AuditLog.objects.filter(tenant=request.tenant)
        qs = _apply_retention(qs, request.tenant)

        action = request.query_params.get('action')
        if action:
            qs = qs.filter(action=action)

        user_id = request.query_params.get('user_id')
        if user_id:
            qs = qs.filter(user_id=user_id)

        resource_type = request.query_params.get('resource_type')
        if resource_type:
            qs = qs.filter(resource_type=resource_type)

        resource_id = request.query_params.get('resource_id')
        if resource_id:
            qs = qs.filter(resource_id=resource_id)

        date_from = request.query_params.get('date_from')
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)

        date_to = request.query_params.get('date_to')
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)

        total = qs.count()
        try:
            page = max(1, int(request.query_params.get('page', 1)))
            per_page = min(100, max(1, int(request.query_params.get('per_page', 50))))
        except (ValueError, TypeError):
            page = 1
            per_page = 50

        offset = (page - 1) * per_page
        logs = qs.select_related('user')[offset:offset + per_page]

        serializer = AuditLogSerializer(logs, many=True)
        return Response({
            'logs': serializer.data,
            'pagination': {'page': page, 'per_page': per_page, 'total': total},
        })


class AuditLogDetailView(APIView):
    permission_classes = [HasFeature('audit_logs'), HasPermission('audit.read')]

    @extend_schema(tags=['audit'], summary='Get audit log entry detail')
    def get(self, request, pk):
        try:
            log = AuditLog.objects.select_related('user').get(pk=pk, tenant=request.tenant)
        except AuditLog.DoesNotExist:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        return Response(AuditLogSerializer(log).data)
