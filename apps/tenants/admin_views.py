"""
Admin views for Client (Tenant) management.

Endpoints:
  GET  /api/v1/admin/clients/              → List all tenants (except own)
  POST /api/v1/admin/clients/<pk>/suspend/ → Toggle tenant active status
"""
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status

from apps.rbac.permissions import HasPermission
from apps.tenants.models import Tenant
from apps.tenants.serializers import ClientListSerializer


class ClientListView(APIView):
    permission_classes = [HasPermission('customers.read')]

    @extend_schema(tags=['admin-clients'], summary='List all tenant clients')
    def get(self, request):
        tenants = (
            Tenant.objects.exclude(id=request.tenant.id)
            .select_related('subscription')
            .prefetch_related('users')
            .order_by('-created_at')
        )
        return Response({'clients': ClientListSerializer(tenants, many=True).data})


class SuspendClientView(APIView):
    permission_classes = [HasPermission('customers.suspend')]

    @extend_schema(tags=['admin-clients'], summary='Toggle tenant active/suspended status')
    def post(self, request, pk):
        tenant = get_object_or_404(Tenant, pk=pk)
        if tenant.id == request.tenant.id:
            return Response(
                {'detail': 'Cannot suspend own tenant.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        active = request.data.get('active')
        if active is None:
            return Response(
                {'detail': 'Field "active" is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        tenant.is_active = bool(active)
        tenant.save(update_fields=['is_active'])
        return Response(ClientListSerializer(tenant).data)
