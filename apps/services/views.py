from rest_framework import status as http_status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Service, TenantService
from .serializers import ServiceSerializer


class ServiceCatalogView(APIView):
    """GET /api/v1/app/services/ — Catálogo completo con available + status."""
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        if not getattr(request, 'tenant', None):
            return Response(
                {'detail': 'X-Tenant-Slug header is required.'},
                status=http_status.HTTP_400_BAD_REQUEST,
            )
        services = Service.objects.filter(is_active=True).order_by('name')
        tenant_services = {
            ts.service_id: ts
            for ts in TenantService.objects.filter(tenant=request.tenant)
        }
        serializer = ServiceSerializer(
            services,
            many=True,
            context={'request': request, 'tenant_services': tenant_services},
        )
        return Response(serializer.data)


class ActiveServicesView(APIView):
    """GET /api/v1/app/services/active/ — Solo servicios adquiridos y activos."""
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        if not getattr(request, 'tenant', None):
            return Response(
                {'detail': 'X-Tenant-Slug header is required.'},
                status=http_status.HTTP_400_BAD_REQUEST,
            )
        active_ts = (
            TenantService.objects
            .filter(tenant=request.tenant, status='active')
            .select_related('service')
        )
        tenant_services = {ts.service_id: ts for ts in active_ts}
        services = [ts.service for ts in active_ts if ts.service.is_active]
        serializer = ServiceSerializer(
            services,
            many=True,
            context={'request': request, 'tenant_services': tenant_services},
        )
        return Response(serializer.data)
