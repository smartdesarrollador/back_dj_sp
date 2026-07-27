"""
Métodos de pago manual: endpoint público (Hub) y administración (Admin Panel).

Una fila por método, editable sin desplegar: lo que se publica al cliente sale de
aquí.
"""
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.rbac.permissions import HasPermission, IsStaffUser
from core.mixins import AuditMixin

from .models import PaymentMethodConfig
from .payment_methods import get_enabled_methods, get_method_config, serialize_public
from .serializers import (
    PaymentMethodConfigSerializer,
    PaymentMethodConfigUpdateSerializer,
)


class PublicPaymentMethodsView(APIView):
    """
    GET /api/v1/public/payment-methods/

    Métodos por los que se puede pagar ahora mismo, en su orden de presentación. Un
    método deshabilitado o sin su dato identificador no aparece: el Hub solo debe
    ofrecer lo que se puede pagar de verdad.
    """
    permission_classes     = [AllowAny]
    authentication_classes: list = []

    @extend_schema(tags=['public'], summary='List available manual payment methods', auth=[])
    def get(self, request: Request) -> Response:
        return Response({
            'methods': [serialize_public(config) for config in get_enabled_methods()],
        })


class AdminPaymentMethodListView(APIView):
    """
    GET /api/v1/admin/payments/methods/

    Todos los métodos, **incluidos los apagados** — al contrario que el endpoint
    público: el admin necesita ver lo que aún no ha configurado para poder configurarlo.

    Datos de plataforma (cross-tenant): IsStaffUser + HasPermission compuestos, nunca el
    permiso RBAC solo (el codename también lo lleva el rol Owner tenant-scoped).
    """
    permission_classes = [IsStaffUser, HasPermission('subscriptions.manage')]

    @extend_schema(tags=['admin'], summary='List payment method configurations')
    def get(self, request: Request) -> Response:
        methods = PaymentMethodConfig.objects.all()
        return Response({'methods': PaymentMethodConfigSerializer(methods, many=True).data})


class AdminPaymentMethodDetailView(AuditMixin, APIView):
    """
    GET   /api/v1/admin/payments/methods/<method>/
    PATCH /api/v1/admin/payments/methods/<method>/

    Nota sobre auditoría: `AuditMixin.log_action` se omite en silencio si la request no
    trae tenant (ver `core/mixins.py`). El Admin envía `X-Tenant-Slug`, así que en la
    práctica siempre lo hay.
    """
    permission_classes = [IsStaffUser, HasPermission('subscriptions.manage')]

    def _get_config(self, method: str) -> PaymentMethodConfig | None:
        return get_method_config(method)

    @extend_schema(tags=['admin'], summary='Get a payment method configuration')
    def get(self, request: Request, method: str) -> Response:
        config = self._get_config(method)
        if config is None:
            return Response(
                {'error': {'code': 'not_found', 'message': 'Payment method not found.'}},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response({'method': PaymentMethodConfigSerializer(config).data})

    @extend_schema(tags=['admin'], summary='Update a payment method configuration')
    def patch(self, request: Request, method: str) -> Response:
        config = self._get_config(method)
        if config is None:
            return Response(
                {'error': {'code': 'not_found', 'message': 'Payment method not found.'}},
                status=status.HTTP_404_NOT_FOUND,
            )

        # `config` va por contexto para que validate() pueda evaluar el estado
        # RESULTANTE del PATCH parcial, no solo lo que viene en el payload.
        serializer = PaymentMethodConfigUpdateSerializer(
            data=request.data, partial=True, context={'config': config},
        )
        serializer.is_valid(raise_exception=True)

        before_enabled = config.is_enabled
        for field, value in serializer.validated_data.items():
            setattr(config, field, value)
        config.save()

        self.log_action(
            request, 'update', 'payment_method', config.method,
            extra={
                'fields': sorted(serializer.validated_data.keys()),
                'is_enabled_before': before_enabled,
                'is_enabled_after': config.is_enabled,
            },
        )
        return Response({'method': PaymentMethodConfigSerializer(config).data})
