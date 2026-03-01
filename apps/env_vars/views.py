"""
EnvVars views — CRUD for encrypted environment variables.

URL namespace: /api/v1/app/env-vars/

Endpoints:
  GET    /app/env-vars/               → list env vars (supports ?environment= ?search=)
  POST   /app/env-vars/               → create env var
  GET    /app/env-vars/<pk>/          → env var detail (value masked)
  PATCH  /app/env-vars/<pk>/          → update env var
  DELETE /app/env-vars/<pk>/          → delete env var
  POST   /app/env-vars/<pk>/reveal/   → reveal decrypted value (audited)
"""
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.env_vars.models import EnvVariable
from apps.env_vars.serializers import EnvVariableCreateUpdateSerializer, EnvVariableSerializer
from apps.rbac.permissions import HasPermission, check_plan_limit
from utils.encryption import decrypt_value

_NOT_FOUND = Response(
    {'error': {'code': 'not_found', 'message': 'Not found.'}}, status=404
)


def _get_object(pk, tenant, user):
    """Return EnvVariable for tenant+user or None."""
    try:
        return EnvVariable.objects.get(pk=pk, tenant=tenant, user=user)
    except EnvVariable.DoesNotExist:
        return None


class EnvVariableListCreateView(APIView):
    permission_classes = [HasPermission('env_vars.read')]

    def get(self, request):
        qs = EnvVariable.objects.filter(tenant=request.tenant, user=request.user)
        environment = request.query_params.get('environment')
        search = request.query_params.get('search')
        if environment:
            qs = qs.filter(environment=environment)
        if search:
            qs = qs.filter(key__icontains=search)
        return Response({'env_vars': EnvVariableSerializer(qs, many=True).data})

    def post(self, request):
        if not request.user.has_perm('env_vars.create'):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()
        count = EnvVariable.objects.filter(tenant=request.tenant, user=request.user).count()
        check_plan_limit(request.user, 'env_vars', count)
        serializer = EnvVariableCreateUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        env_var = EnvVariable.objects.create(
            tenant=request.tenant,
            user=request.user,
            **serializer.validated_data,
        )
        return Response(EnvVariableSerializer(env_var).data, status=status.HTTP_201_CREATED)


class EnvVariableDetailView(APIView):
    permission_classes = [HasPermission('env_vars.read')]

    def get(self, request, pk):
        env_var = _get_object(pk, request.tenant, request.user)
        if not env_var:
            return _NOT_FOUND
        return Response({'env_var': EnvVariableSerializer(env_var).data})

    def patch(self, request, pk):
        if not request.user.has_perm('env_vars.update'):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()
        env_var = _get_object(pk, request.tenant, request.user)
        if not env_var:
            return _NOT_FOUND
        serializer = EnvVariableCreateUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        # If value is being updated, reset encryption flag so save() re-encrypts
        if 'value' in data:
            env_var.is_encrypted = False
        for field, value in data.items():
            setattr(env_var, field, value)
        env_var.save()
        return Response(EnvVariableSerializer(env_var).data)

    def delete(self, request, pk):
        if not request.user.has_perm('env_vars.delete'):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()
        env_var = _get_object(pk, request.tenant, request.user)
        if not env_var:
            return _NOT_FOUND
        env_var.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class EnvVariableRevealView(APIView):
    permission_classes = [HasPermission('env_vars.reveal')]

    def post(self, request, pk):
        env_var = _get_object(pk, request.tenant, request.user)
        if not env_var:
            return _NOT_FOUND

        plain = decrypt_value(env_var.value)

        try:
            from apps.audit.models import AuditLog
            AuditLog.objects.create(
                tenant=request.tenant,
                user=request.user,
                action='env_vars.reveal',
                resource_type='EnvVariable',
                resource_id=str(env_var.id),
                ip_address=request.META.get('REMOTE_ADDR'),
            )
        except Exception:
            # Audit failure must not block the response
            pass

        return Response({'key': env_var.key, 'value': plain})
