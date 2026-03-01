"""
RBAC views.

Endpoints:
  GET  /api/v1/features/                         → Plan features and limits
  GET  /api/v1/admin/roles/                      → List roles
  POST /api/v1/admin/roles/create/               → Create custom role
  GET  /api/v1/admin/roles/<pk>/                 → Role detail
  PATCH /api/v1/admin/roles/<pk>/update/         → Update custom role
  DELETE /api/v1/admin/roles/<pk>/delete/        → Delete custom role
  PUT  /api/v1/admin/roles/<pk>/permissions/     → Replace role permissions
  GET  /api/v1/admin/permissions/                → List all permissions
"""
from django.db import transaction
from django.db.models import Q
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.rbac.models import Permission, Role, RolePermission
from apps.rbac.permissions import HasFeature, HasPermission, check_plan_limit
from apps.rbac.serializers import (
    PermissionSerializer,
    RoleCreateUpdateSerializer,
    RoleSerializer,
)
from utils.plans import PLAN_FEATURES

# Claves que son límites operacionales (no feature flags booleanos)
_OPERATIONAL_LIMITS = {'audit_log_days', 'storage_gb', 'api_calls_per_month'}


class FeaturesView(APIView):
    """
    Retorna las features y límites del plan activo del tenant autenticado.

    Response:
        {
            "plan": "professional",
            "features": {
                "custom_roles": true,
                "mfa": true,
                ...
            },
            "limits": {
                "users": 25,
                "projects": null,
                "storage_gb": 20,
                "api_calls_per_month": 100000
            }
        }
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=['app-features'], summary='Get plan features and limits')
    def get(self, request):
        tenant = getattr(request, 'tenant', None)
        plan = tenant.plan if tenant else 'free'
        plan_config = PLAN_FEATURES.get(plan, PLAN_FEATURES['free'])

        feature_flags = {
            k: v
            for k, v in plan_config.items()
            if not k.startswith('max_') and k not in _OPERATIONAL_LIMITS
        }

        limits = {
            'users': plan_config.get('max_users'),
            'projects': plan_config.get('max_projects'),
            'storage_gb': plan_config.get('storage_gb'),
            'api_calls_per_month': plan_config.get('api_calls_per_month'),
        }

        return Response({'plan': plan, 'features': feature_flags, 'limits': limits})


# ─── Admin Role Views ──────────────────────────────────────────────────────────

class RoleListView(APIView):
    permission_classes = [HasPermission('roles.read')]

    @extend_schema(tags=['admin-roles'], summary='List roles')
    def get(self, request):
        roles = Role.objects.filter(
            Q(is_system_role=True) | Q(tenant=request.tenant)
        ).prefetch_related('role_permissions__permission', 'user_roles')
        return Response({'roles': RoleSerializer(roles, many=True).data})


class RoleCreateView(APIView):
    permission_classes = [HasPermission('roles.create'), HasFeature('custom_roles')]

    @extend_schema(
        tags=['admin-roles'],
        summary='Create custom role',
        responses={
            201: OpenApiResponse(description='Role created'),
            402: OpenApiResponse(description='Plan limit exceeded'),
        },
    )
    def post(self, request):
        custom_count = Role.objects.filter(tenant=request.tenant, is_system_role=False).count()
        check_plan_limit(request.user, 'custom_roles', custom_count)

        serializer = RoleCreateUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        permission_ids = serializer.validated_data.pop('permission_ids', [])

        role = Role.objects.create(
            tenant=request.tenant,
            is_system_role=False,
            **serializer.validated_data,
        )

        if permission_ids:
            perms = Permission.objects.filter(id__in=permission_ids)
            RolePermission.objects.bulk_create([
                RolePermission(role=role, permission=p) for p in perms
            ])

        return Response(
            RoleSerializer(role).data,
            status=status.HTTP_201_CREATED,
        )


class RoleDetailView(APIView):
    permission_classes = [HasPermission('roles.read')]

    @extend_schema(tags=['admin-roles'], summary='Get role detail')
    def get(self, request, pk):
        try:
            role = Role.objects.prefetch_related(
                'role_permissions__permission', 'user_roles'
            ).get(pk=pk)
        except Role.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        if not role.is_system_role and role.tenant_id != request.tenant.id:
            return Response(status=status.HTTP_404_NOT_FOUND)

        return Response(RoleSerializer(role).data)


class RoleUpdateView(APIView):
    permission_classes = [HasPermission('roles.update')]

    @extend_schema(tags=['admin-roles'], summary='Update custom role')
    def patch(self, request, pk):
        try:
            role = Role.objects.get(pk=pk)
        except Role.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        if role.is_system_role:
            return Response(
                {'detail': 'Cannot modify system roles.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        if role.tenant_id != request.tenant.id:
            return Response(status=status.HTTP_404_NOT_FOUND)

        serializer = RoleCreateUpdateSerializer(role, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.validated_data.pop('permission_ids', None)
        serializer.save()
        role.refresh_from_db()
        return Response(RoleSerializer(role).data)


class RoleDeleteView(APIView):
    permission_classes = [HasPermission('roles.delete')]

    @extend_schema(tags=['admin-roles'], summary='Delete custom role')
    def delete(self, request, pk):
        try:
            role = Role.objects.get(pk=pk)
        except Role.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        if role.is_system_role:
            return Response(
                {'detail': 'Cannot delete system roles.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        if role.tenant_id != request.tenant.id:
            return Response(status=status.HTTP_404_NOT_FOUND)

        role.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class RolePermissionsUpdateView(APIView):
    permission_classes = [HasPermission('roles.update')]

    @extend_schema(tags=['admin-roles'], summary='Replace role permissions')
    def put(self, request, pk):
        try:
            role = Role.objects.get(pk=pk)
        except Role.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        if role.is_system_role:
            return Response(
                {'detail': 'Cannot modify system roles.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        if role.tenant_id != request.tenant.id:
            return Response(status=status.HTTP_404_NOT_FOUND)

        permission_ids = request.data.get('permission_ids', [])
        perms = list(Permission.objects.filter(id__in=permission_ids))
        if len(perms) != len(permission_ids):
            return Response(
                {'detail': 'One or more permission IDs not found.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            RolePermission.objects.filter(role=role).delete()
            RolePermission.objects.bulk_create([
                RolePermission(role=role, permission=p) for p in perms
            ])

        role.refresh_from_db()
        return Response(RoleSerializer(role).data)


# ─── Admin Permission Views ────────────────────────────────────────────────────

class PermissionListView(APIView):
    permission_classes = [HasPermission('roles.read')]

    @extend_schema(tags=['admin-roles'], summary='List all permissions')
    def get(self, request):
        perms = Permission.objects.all().order_by('resource', 'codename')
        return Response({'permissions': PermissionSerializer(perms, many=True).data})
