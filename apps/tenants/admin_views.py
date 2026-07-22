"""
Admin views for Client (Tenant) management.

Endpoints:
  GET   /api/v1/admin/clients/              → List all tenants (except own)
  POST  /api/v1/admin/clients/<pk>/suspend/ → Toggle tenant active status
  GET   /api/v1/admin/organization/         → Get own tenant branding data
  PATCH /api/v1/admin/organization/         → Update name, color, logo, favicon
"""
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status

from apps.rbac.permissions import HasPermission, IsStaffUser
from apps.tenants.models import Tenant
from apps.tenants.serializers import ClientListSerializer, OrganizationSerializer
from utils.uploads import validate_upload


class ClientListView(APIView):
    # IsStaffUser is required in addition to the RBAC permission: 'customers.read'
    # is also granted to the tenant-scoped system 'Owner' role, but this view
    # returns every OTHER tenant in the system — never gate it on RBAC alone.
    permission_classes = [IsStaffUser, HasPermission('customers.read')]

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
    # Same reasoning as ClientListView — this mutates another tenant's status.
    permission_classes = [IsStaffUser, HasPermission('customers.suspend')]

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


class OrganizationView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes     = [MultiPartParser, FormParser, JSONParser]

    @extend_schema(tags=['admin-organization'], summary='Get own tenant branding')
    def get(self, request):
        serializer = OrganizationSerializer(
            request.tenant, context={'request': request}
        )
        return Response(serializer.data)

    @extend_schema(tags=['admin-organization'], summary='Update own tenant branding')
    def patch(self, request):
        tenant = request.tenant
        update_fields = []
        for key in ('logo', 'favicon'):
            if key in request.FILES:
                validate_upload(request.FILES[key], category='tenant_branding', tenant=tenant)
        if 'name' in request.data:
            tenant.name = request.data['name']
            update_fields.append('name')
        if 'logo' in request.FILES:
            tenant.logo = request.FILES['logo']
            update_fields.append('logo')
        if 'favicon' in request.FILES:
            tenant.favicon = request.FILES['favicon']
            update_fields.append('favicon')
        if 'primary_color' in request.data:
            tenant.branding = {**tenant.branding, 'primary_color': request.data['primary_color']}
            update_fields.append('branding')
        if update_fields:
            tenant.save(update_fields=update_fields)
        return Response(OrganizationSerializer(tenant, context={'request': request}).data)
