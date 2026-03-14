"""SSO views — Hub generates a short-lived opaque token; destination validates it server-to-server."""
import secrets
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.models import AuditLog
from apps.auth_app.models import SSOToken
from apps.auth_app.serializers import (
    SSOTokenRequestSerializer,
    SSOValidateRequestSerializer,
    UserSerializer,
)
from apps.auth_app.tokens import TenantRefreshToken

User = get_user_model()


def _get_client_ip(request: Request) -> str | None:
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR') or None


class SSOTokenView(APIView):
    """
    POST /api/v1/auth/sso/token/

    Authenticated Hub user requests a single-use 60s SSO token for a destination service.
    Returns: { sso_token, redirect_url, expires_in }
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=['hub-sso'],
        summary='Genera token SSO de corta duración (TTL 60s, single-use)',
        request=SSOTokenRequestSerializer,
        responses={
            200: OpenApiResponse(description='{ sso_token, redirect_url, expires_in }'),
            403: OpenApiResponse(description='Tenant inactivo o servicio no adquirido'),
            404: OpenApiResponse(description='Servicio no encontrado'),
        },
    )
    def post(self, request: Request) -> Response:
        serializer = SSOTokenRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        service_slug: str = serializer.validated_data['service']

        user = request.user
        tenant = user.tenant

        if not tenant or not tenant.is_active:
            return Response(
                {'detail': 'Tenant is suspended or inactive.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        from apps.services.models import Service, TenantService

        try:
            service = Service.objects.get(slug=service_slug, is_active=True)
        except Service.DoesNotExist:
            return Response({'detail': 'Service not found.'}, status=status.HTTP_404_NOT_FOUND)

        try:
            tenant_service = TenantService.objects.get(tenant=tenant, service=service)
        except TenantService.DoesNotExist:
            return Response(
                {'detail': 'Service not acquired by tenant.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        if tenant_service.status != 'active':
            return Response(
                {'detail': 'Service is suspended for this tenant.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        token_value = secrets.token_hex(32)
        expires_at = timezone.now() + timedelta(seconds=60)

        sso_token = SSOToken.objects.create(
            user=user,
            tenant=tenant,
            service=service_slug,
            token=token_value,
            expires_at=expires_at,
        )

        AuditLog.objects.create(
            user=user,
            tenant=tenant,
            action='sso.token_created',
            resource_type='SSOToken',
            resource_id=str(sso_token.id),
            ip_address=_get_client_ip(request),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
            extra={'service': service_slug},
        )

        redirect_url = (
            service.url_template.format(subdomain=tenant.subdomain)
            + f'?sso_token={token_value}'
        )

        return Response({
            'sso_token': token_value,
            'redirect_url': redirect_url,
            'expires_in': 60,
        })


class SSOValidateView(APIView):
    """
    POST /api/v1/auth/sso/validate/

    Destination service validates the SSO token (server-to-server, no auth required).
    Token is marked used (single-use). Returns JWT tokens + user data.
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        tags=['hub-sso'],
        summary='Valida y consume token SSO (server-to-server, sin auth)',
        request=SSOValidateRequestSerializer,
        responses={
            200: OpenApiResponse(description='{ access_token, refresh_token, user }'),
            404: OpenApiResponse(description='Token no encontrado'),
            410: OpenApiResponse(description='Token ya usado o expirado'),
        },
    )
    def post(self, request: Request) -> Response:
        serializer = SSOValidateRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token_value: str = serializer.validated_data['sso_token']

        now = timezone.now()

        try:
            with transaction.atomic():
                sso_token = SSOToken.objects.select_for_update().select_related(
                    'user', 'tenant'
                ).get(token=token_value)

                if sso_token.used_at is not None:
                    self._log_invalid(request, sso_token, 'already_used')
                    return Response(
                        {'detail': 'Token already used.'},
                        status=status.HTTP_410_GONE,
                    )

                if sso_token.expires_at < now:
                    tenant_ref = sso_token.tenant
                    user_ref = sso_token.user
                    sso_token.delete()
                    self._log_invalid_with_refs(request, tenant_ref, user_ref, token_value, 'expired')
                    return Response(
                        {'detail': 'Token has expired.'},
                        status=status.HTTP_410_GONE,
                    )

                sso_token.used_at = now
                sso_token.save(update_fields=['used_at'])

                user = sso_token.user
                tenant = sso_token.tenant
                service_slug = sso_token.service

        except SSOToken.DoesNotExist:
            return Response({'detail': 'Token not found.'}, status=status.HTTP_404_NOT_FOUND)

        refresh = TenantRefreshToken.for_user(user)
        refresh['service'] = service_slug

        User.objects.filter(pk=user.pk).update(last_login=now)

        self._log_validated(request, sso_token, tenant, user, service_slug)

        return Response({
            'access_token': str(refresh.access_token),
            'refresh_token': str(refresh),
            'user': UserSerializer(user).data,
        })

    def _log_invalid(self, request: Request, sso_token: SSOToken, reason: str) -> None:
        AuditLog.objects.create(
            user=sso_token.user,
            tenant=sso_token.tenant,
            action='sso.token_invalid',
            resource_type='SSOToken',
            resource_id=str(sso_token.id),
            ip_address=_get_client_ip(request),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
            extra={'reason': reason, 'service': sso_token.service},
        )

    def _log_invalid_with_refs(
        self,
        request: Request,
        tenant: object,
        user: object,
        token_value: str,
        reason: str,
    ) -> None:
        AuditLog.objects.create(
            user=user,
            tenant=tenant,
            action='sso.token_invalid',
            resource_type='SSOToken',
            resource_id='',
            ip_address=_get_client_ip(request),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
            extra={'reason': reason, 'token_prefix': token_value[:8]},
        )

    def _log_validated(
        self,
        request: Request,
        sso_token: SSOToken,
        tenant: object,
        user: object,
        service_slug: str,
    ) -> None:
        AuditLog.objects.create(
            user=user,
            tenant=tenant,
            action='sso.token_validated',
            resource_type='SSOToken',
            resource_id=str(sso_token.id),
            ip_address=_get_client_ip(request),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
            extra={'service': service_slug},
        )
