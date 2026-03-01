"""
Sharing views — resource sharing CRUD with cascade inheritance.

URL namespace: /api/v1/app/sharing/

Endpoints:
  GET    /app/sharing/               → list shares for a resource
  POST   /app/sharing/               → create a share (+ cascade for projects)
  GET    /app/sharing/shared-with-me/ → shares received by the current user
  PATCH  /app/sharing/<pk>/          → update permission level
  DELETE /app/sharing/<pk>/delete/   → revoke share (+ cascade for projects)
"""
import uuid

from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.models import AuditLog
from apps.projects.models import Project
from apps.rbac.permissions import HasFeature, HasPermission, check_plan_limit
from apps.sharing.models import Share
from apps.sharing.serializers import (
    ShareCreateSerializer,
    ShareSerializer,
    SharedWithMeSerializer,
)

User = get_user_model()

_NOT_FOUND = Response(
    {'error': {'code': 'not_found', 'message': 'Resource not found.'}},
    status=status.HTTP_404_NOT_FOUND,
)


# ─── Cascade Helpers ──────────────────────────────────────────────────────────

def _collect_resource_ids(project) -> list[uuid.UUID]:
    """Collect all section and item UUIDs that belong to a project."""
    ids: list[uuid.UUID] = []
    for section in project.sections.prefetch_related('items').all():
        ids.append(section.id)
        for item in section.items.all():
            ids.append(item.id)
    return ids


def _cascade_create(project, shared_by, shared_with, permission_level, tenant) -> None:
    """Create inherited shares for all sections and items of the project."""
    for section in project.sections.prefetch_related('items').all():
        Share.objects.get_or_create(
            tenant=tenant,
            resource_type='section',
            resource_id=section.id,
            shared_with=shared_with,
            defaults={
                'shared_by': shared_by,
                'permission_level': permission_level,
                'is_inherited': True,
            },
        )
        for item in section.items.all():
            Share.objects.get_or_create(
                tenant=tenant,
                resource_type='item',
                resource_id=item.id,
                shared_with=shared_with,
                defaults={
                    'shared_by': shared_by,
                    'permission_level': permission_level,
                    'is_inherited': True,
                },
            )


def _cascade_update(project, shared_with, new_level, tenant) -> None:
    """Propagate new permission_level to inherited child shares only."""
    child_ids = _collect_resource_ids(project)
    if child_ids:
        Share.objects.filter(
            tenant=tenant,
            shared_with=shared_with,
            resource_type__in=['section', 'item'],
            is_inherited=True,
            resource_id__in=child_ids,
        ).update(permission_level=new_level)


def _cascade_delete(project, shared_with, tenant) -> None:
    """Delete all child shares (inherited and local overrides) for the project."""
    child_ids = _collect_resource_ids(project)
    if child_ids:
        Share.objects.filter(
            tenant=tenant,
            shared_with=shared_with,
            resource_id__in=child_ids,
        ).delete()


# ─── Views ────────────────────────────────────────────────────────────────────

class ShareListCreateView(APIView):
    """
    GET  /app/sharing/  — list shares for a given resource (query params required)
    POST /app/sharing/  — create a share with optional cascade
    """
    def get_permissions(self):
        if self.request.method == 'POST':
            return [
                HasFeature('sharing')(),
                HasPermission('projects.share')(),
            ]
        return [HasPermission('projects.read')()]

    def get(self, request):
        resource_type = request.query_params.get('resource_type')
        resource_id = request.query_params.get('resource_id')
        qs = Share.objects.filter(tenant=request.tenant)
        if resource_type:
            qs = qs.filter(resource_type=resource_type)
        if resource_id:
            qs = qs.filter(resource_id=resource_id)
        serializer = ShareSerializer(qs.select_related('shared_by', 'shared_with'), many=True)
        return Response({'shares': serializer.data})

    @transaction.atomic
    def post(self, request):
        serializer = ShareCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'error': {'code': 'validation_error', 'details': serializer.errors}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = serializer.validated_data
        resource_type = data['resource_type']
        resource_id = data['resource_id']
        permission_level = data['permission_level']
        expires_at = data.get('expires_at')

        # Resolve shared_with by email within the same tenant
        try:
            shared_with = User.objects.get(
                email=data['shared_with_email'], tenant=request.tenant
            )
        except User.DoesNotExist:
            return _NOT_FOUND

        # Validate resource belongs to tenant (project only; sections/items inherit)
        if resource_type == 'project':
            try:
                project = Project.objects.get(pk=resource_id, tenant=request.tenant)
            except Project.DoesNotExist:
                return _NOT_FOUND

            # Plan limit: count existing shares for this project
            existing_count = Share.objects.filter(
                tenant=request.tenant,
                resource_type='project',
                resource_id=resource_id,
            ).count()
            check_plan_limit(request.user, 'shares_per_project', existing_count)
        else:
            project = None

        share, created = Share.objects.get_or_create(
            tenant=request.tenant,
            resource_type=resource_type,
            resource_id=resource_id,
            shared_with=shared_with,
            defaults={
                'shared_by': request.user,
                'permission_level': permission_level,
                'is_inherited': False,
                'expires_at': expires_at,
            },
        )

        if resource_type == 'project' and created and project:
            _cascade_create(project, request.user, shared_with, permission_level, request.tenant)

        try:
            AuditLog.objects.create(
                tenant=request.tenant,
                user=request.user,
                action='share.created',
                resource_type=resource_type,
                resource_id=str(resource_id),
                ip_address=request.META.get('REMOTE_ADDR'),
                extra={'shared_with': data['shared_with_email'], 'permission_level': permission_level},
            )
        except Exception:
            pass

        out = ShareSerializer(share)
        return Response({'share': out.data}, status=status.HTTP_201_CREATED)


class ShareUpdateView(APIView):
    """PATCH /app/sharing/<pk>/ — update permission level."""
    permission_classes = [HasPermission('projects.share')]

    @transaction.atomic
    def patch(self, request, pk):
        try:
            share = Share.objects.get(pk=pk, tenant=request.tenant)
        except Share.DoesNotExist:
            return _NOT_FOUND

        new_level = request.data.get('permission_level')
        if new_level not in dict(Share.PERMISSION_LEVELS):
            return Response(
                {'error': {'code': 'validation_error', 'message': 'Invalid permission_level.'}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        share.permission_level = new_level
        share.save(update_fields=['permission_level', 'updated_at'])

        if share.resource_type == 'project':
            try:
                project = Project.objects.get(pk=share.resource_id, tenant=request.tenant)
                _cascade_update(project, share.shared_with, new_level, request.tenant)
            except Project.DoesNotExist:
                pass

        try:
            AuditLog.objects.create(
                tenant=request.tenant,
                user=request.user,
                action='share.updated',
                resource_type=share.resource_type,
                resource_id=str(share.resource_id),
                ip_address=request.META.get('REMOTE_ADDR'),
                extra={'new_permission_level': new_level},
            )
        except Exception:
            pass

        return Response({'share': ShareSerializer(share).data})


class ShareDeleteView(APIView):
    """DELETE /app/sharing/<pk>/delete/ — revoke a share."""
    permission_classes = [HasPermission('projects.share')]

    @transaction.atomic
    def delete(self, request, pk):
        try:
            share = Share.objects.get(pk=pk, tenant=request.tenant)
        except Share.DoesNotExist:
            return _NOT_FOUND

        resource_type = share.resource_type
        resource_id = share.resource_id
        shared_with = share.shared_with

        if resource_type == 'project':
            try:
                project = Project.objects.get(pk=resource_id, tenant=request.tenant)
                _cascade_delete(project, shared_with, request.tenant)
            except Project.DoesNotExist:
                pass

        share.delete()

        try:
            AuditLog.objects.create(
                tenant=request.tenant,
                user=request.user,
                action='share.revoked',
                resource_type=resource_type,
                resource_id=str(resource_id),
                ip_address=request.META.get('REMOTE_ADDR'),
                extra={'shared_with': shared_with.email},
            )
        except Exception:
            pass

        return Response(status=status.HTTP_204_NO_CONTENT)


class SharedWithMeView(APIView):
    """GET /app/sharing/shared-with-me/ — shares received by the current user."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = Share.objects.filter(
            shared_with=request.user, tenant=request.tenant
        ).select_related('shared_by', 'shared_with')

        resource_type = request.query_params.get('resource_type')
        if resource_type:
            qs = qs.filter(resource_type=resource_type)

        serializer = SharedWithMeSerializer(qs, many=True)
        return Response({'shares': serializer.data})
