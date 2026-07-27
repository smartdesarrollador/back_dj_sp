"""
Configuración de moneda de la plataforma: endpoint público (Hub) y admin.

USD es la moneda base. Estos endpoints solo dictan cómo PRESENTAR importes en
otra moneda — no existe ningún precio almacenado en PEN, y el monto que se cobra
lo sigue calculando el backend en USD (utils.promotions.get_plan_price).
"""
from decimal import Decimal

from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.rbac.permissions import HasPermission, IsStaffUser
from core.mixins import AuditMixin
from utils.currency import BASE_CURRENCY, SUPPORTED_CURRENCIES, get_currency_config

from .models import CurrencyConfig
from .serializers import CurrencyConfigSerializer, CurrencyConfigUpdateSerializer


class CurrencyPublicView(APIView):
    """
    GET /api/v1/public/currency/

    Forma extensible a N monedas aunque el almacenamiento sea una sola columna:
    añadir una moneda no cambiará el contrato del cliente. `rates` incluye USD
    ('1.0000') para que el conversor del Hub sea `amount * Number(rates[c])` sin
    caso especial para la moneda base.

    Respuesta cacheada 5 min (utils.currency), invalidada al guardar.
    """
    permission_classes     = [AllowAny]
    authentication_classes: list = []

    @extend_schema(tags=['public'], summary='Get platform currency configuration', auth=[])
    def get(self, request: Request) -> Response:
        cfg = get_currency_config()
        return Response({
            'base_currency': BASE_CURRENCY,
            'supported_currencies': list(SUPPORTED_CURRENCIES),
            'rates': {
                'USD': '1.0000',
                'PEN': cfg['usd_to_pen'],
            },
            'default_display_currency': cfg['default_display_currency'],
            'updated_at': cfg['updated_at'],
        })


class AdminCurrencyConfigView(AuditMixin, APIView):
    """
    GET   /api/v1/admin/billing/currency/
    PATCH /api/v1/admin/billing/currency/

    Datos de plataforma (cross-tenant): IsStaffUser + HasPermission compuestos,
    nunca el permiso RBAC solo — el codename 'subscriptions.manage' también lo
    lleva el rol Owner tenant-scoped (ver docstring de IsStaffUser).

    Nota sobre auditoría: AuditMixin.log_action se omite en silencio si no hay
    tenant en la request (core/mixins.py). El Admin Panel envía X-Tenant-Slug, así
    que en la práctica siempre lo hay, pero un cliente que no lo mande perdería el
    rastro del cambio.
    """
    permission_classes = [IsStaffUser, HasPermission('subscriptions.manage')]

    @extend_schema(tags=['admin'], summary='Get currency configuration')
    def get(self, request: Request) -> Response:
        return Response({'currency': CurrencyConfigSerializer(CurrencyConfig.get()).data})

    @extend_schema(tags=['admin'], summary='Update currency configuration')
    def patch(self, request: Request) -> Response:
        cfg = CurrencyConfig.get()
        serializer = CurrencyConfigUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        before = cfg.usd_to_pen
        for field, value in serializer.validated_data.items():
            setattr(cfg, field, value)
        cfg.source     = 'manual'
        cfg.updated_by = request.user
        cfg.save()  # invalida la caché

        self.log_action(
            request, 'update', 'currency_config', '1',
            extra={
                'usd_to_pen_before': str(before),
                'usd_to_pen_after':  str(cfg.usd_to_pen),
                'fields': sorted(serializer.validated_data.keys()),
            },
        )
        return Response({'currency': CurrencyConfigSerializer(cfg).data})
