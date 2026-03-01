"""
SSH Keys views — CRUD for SSH key pairs.

URL namespace: /api/v1/app/ssh-keys/

Endpoints:
  GET    /app/ssh-keys/          → list SSH keys (supports ?algorithm= ?search=)
  POST   /app/ssh-keys/          → create SSH key
  GET    /app/ssh-keys/<pk>/     → SSH key detail
  PATCH  /app/ssh-keys/<pk>/     → update SSH key
  DELETE /app/ssh-keys/<pk>/     → delete SSH key
"""
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.rbac.permissions import HasPermission, check_plan_limit
from apps.ssh_keys.models import SSHKey
from apps.ssh_keys.serializers import SSHKeyCreateSerializer, SSHKeySerializer

_NOT_FOUND = Response(
    {'error': {'code': 'not_found', 'message': 'Not found.'}}, status=404
)


def _get_object(pk, tenant, user):
    """Return SSHKey for tenant+user or None."""
    try:
        return SSHKey.objects.get(pk=pk, tenant=tenant, user=user)
    except SSHKey.DoesNotExist:
        return None


class SSHKeyListCreateView(APIView):
    permission_classes = [HasPermission('ssh_keys.read')]

    def get(self, request):
        qs = SSHKey.objects.filter(tenant=request.tenant, user=request.user)
        algorithm = request.query_params.get('algorithm')
        search = request.query_params.get('search')
        if algorithm:
            qs = qs.filter(algorithm=algorithm)
        if search:
            qs = qs.filter(name__icontains=search)
        return Response({'ssh_keys': SSHKeySerializer(qs, many=True).data})

    def post(self, request):
        if not request.user.has_perm('ssh_keys.create'):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()
        count = SSHKey.objects.filter(tenant=request.tenant, user=request.user).count()
        check_plan_limit(request.user, 'ssh_keys', count)
        serializer = SSHKeyCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ssh_key = SSHKey.objects.create(
            tenant=request.tenant,
            user=request.user,
            **serializer.validated_data,
        )
        return Response(SSHKeySerializer(ssh_key).data, status=status.HTTP_201_CREATED)


class SSHKeyDetailView(APIView):
    permission_classes = [HasPermission('ssh_keys.read')]

    def get(self, request, pk):
        ssh_key = _get_object(pk, request.tenant, request.user)
        if not ssh_key:
            return _NOT_FOUND
        return Response({'ssh_key': SSHKeySerializer(ssh_key).data})

    def patch(self, request, pk):
        if not request.user.has_perm('ssh_keys.update'):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()
        ssh_key = _get_object(pk, request.tenant, request.user)
        if not ssh_key:
            return _NOT_FOUND
        serializer = SSHKeyCreateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        # If private_key is updated, reset encryption flag so save() re-encrypts
        if 'private_key' in data and data['private_key']:
            ssh_key.is_encrypted = False
        for field, value in data.items():
            setattr(ssh_key, field, value)
        ssh_key.save()
        return Response(SSHKeySerializer(ssh_key).data)

    def delete(self, request, pk):
        if not request.user.has_perm('ssh_keys.delete'):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()
        ssh_key = _get_object(pk, request.tenant, request.user)
        if not ssh_key:
            return _NOT_FOUND
        ssh_key.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
