"""
SSL Certs views — CRUD for SSL certificate tracking.

URL namespace: /api/v1/app/ssl-certs/

Endpoints:
  GET    /app/ssl-certs/        → list SSL certs (supports ?domain= ?status=)
  POST   /app/ssl-certs/        → create SSL cert
  GET    /app/ssl-certs/<pk>/   → SSL cert detail
  PATCH  /app/ssl-certs/<pk>/   → update SSL cert
  DELETE /app/ssl-certs/<pk>/   → delete SSL cert
"""
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.rbac.permissions import HasPermission, check_plan_limit
from apps.ssl_certs.models import SSLCertificate
from apps.ssl_certs.serializers import (
    SSLCertificateCreateUpdateSerializer,
    SSLCertificateSerializer,
)

_NOT_FOUND = Response(
    {'error': {'code': 'not_found', 'message': 'Not found.'}}, status=404
)


def _get_object(pk, tenant, user):
    """Return SSLCertificate for tenant+user or None."""
    try:
        return SSLCertificate.objects.get(pk=pk, tenant=tenant, user=user)
    except SSLCertificate.DoesNotExist:
        return None


class SSLCertificateListCreateView(APIView):
    permission_classes = [HasPermission('ssl_certs.read')]

    def get(self, request):
        qs = SSLCertificate.objects.filter(tenant=request.tenant, user=request.user)
        domain = request.query_params.get('domain')
        status_filter = request.query_params.get('status')
        if domain:
            qs = qs.filter(domain__icontains=domain)
        certs = list(qs)
        if status_filter:
            certs = [c for c in certs if c.status == status_filter]
        return Response({'ssl_certs': SSLCertificateSerializer(certs, many=True).data})

    def post(self, request):
        if not request.user.has_perm('ssl_certs.create'):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()
        count = SSLCertificate.objects.filter(tenant=request.tenant, user=request.user).count()
        check_plan_limit(request.user, 'ssl_certs', count)
        serializer = SSLCertificateCreateUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        cert = SSLCertificate.objects.create(
            tenant=request.tenant,
            user=request.user,
            **serializer.validated_data,
        )
        return Response(SSLCertificateSerializer(cert).data, status=status.HTTP_201_CREATED)


class SSLCertificateDetailView(APIView):
    permission_classes = [HasPermission('ssl_certs.read')]

    def get(self, request, pk):
        cert = _get_object(pk, request.tenant, request.user)
        if not cert:
            return _NOT_FOUND
        return Response({'ssl_cert': SSLCertificateSerializer(cert).data})

    def patch(self, request, pk):
        if not request.user.has_perm('ssl_certs.update'):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()
        cert = _get_object(pk, request.tenant, request.user)
        if not cert:
            return _NOT_FOUND
        serializer = SSLCertificateCreateUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        for field, value in serializer.validated_data.items():
            setattr(cert, field, value)
        cert.save()
        return Response(SSLCertificateSerializer(cert).data)

    def delete(self, request, pk):
        if not request.user.has_perm('ssl_certs.delete'):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()
        cert = _get_object(pk, request.tenant, request.user)
        if not cert:
            return _NOT_FOUND
        cert.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
