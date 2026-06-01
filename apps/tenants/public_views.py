"""Public (unauthenticated) tenant branding endpoint."""
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.tenants.models import Tenant
from apps.tenants.serializers import OrganizationSerializer


class PublicBrandingView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(tags=['public'], summary='Get tenant branding by subdomain')
    def get(self, request):
        subdomain = request.query_params.get('subdomain', '')
        if not subdomain:
            return Response({'logo_url': None, 'favicon_url': None, 'name': None, 'primary_color': None})
        try:
            tenant = Tenant.objects.get(subdomain=subdomain, is_active=True)
        except Tenant.DoesNotExist:
            return Response({'logo_url': None, 'favicon_url': None, 'name': None, 'primary_color': None})
        serializer = OrganizationSerializer(tenant, context={'request': request})
        return Response({
            'name': serializer.data['name'],
            'logo_url': serializer.data['logo_url'],
            'favicon_url': serializer.data['favicon_url'],
            'primary_color': serializer.data['primary_color'],
        })
