"""
Endpoints admin del CRUD de promociones (códigos de descuento).

Datos de plataforma (cross-tenant): siempre IsStaffUser + HasPermission
compuestos — nunca el permiso RBAC solo (los codenames también los tiene
el rol Owner tenant-scoped).
"""
from django.db.models import Avg, Count, Q, QuerySet, Sum
from rest_framework import status
from rest_framework.generics import ListCreateAPIView, RetrieveUpdateDestroyAPIView
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.rbac.permissions import HasPermission, IsStaffUser
from core.mixins import AuditMixin

from .models import Promotion, PromotionRedemption
from .serializers import PromotionSerializer, PromotionWriteSerializer

_METRIC_ANNOTATIONS = {
    'confirmed_count': Count(
        'redemptions', filter=Q(redemptions__status='confirmed'), distinct=True
    ),
    'released_count': Count(
        'redemptions', filter=Q(redemptions__status='released'), distinct=True
    ),
    'revenue_sum': Sum(
        'redemptions__final_amount', filter=Q(redemptions__status='confirmed')
    ),
    'discount_avg': Avg(
        'redemptions__discount_amount', filter=Q(redemptions__status='confirmed')
    ),
}


def _annotated_promotions() -> QuerySet[Promotion]:
    return Promotion.objects.annotate(**_METRIC_ANNOTATIONS)


class AdminPromotionListCreateView(AuditMixin, ListCreateAPIView):
    """
    GET  /api/v1/admin/promotions/ — lista todas las promociones con métricas
    POST /api/v1/admin/promotions/ — crear promoción
    """
    permission_classes = [IsStaffUser, HasPermission('promotions.manage')]
    pagination_class = None

    def get_serializer_class(self):
        return PromotionWriteSerializer if self.request.method == 'POST' else PromotionSerializer

    def get_queryset(self) -> QuerySet[Promotion]:
        return _annotated_promotions()

    def list(self, request: Request, *args, **kwargs) -> Response:
        serializer = self.get_serializer(self.get_queryset(), many=True)
        return Response({'promotions': serializer.data})

    def perform_create(self, serializer: PromotionWriteSerializer) -> None:
        promotion = serializer.save()
        self.log_action(
            self.request, 'create', 'promotion', str(promotion.id),
            extra={'code': promotion.code, 'type': promotion.type},
        )


class AdminPromotionDetailView(AuditMixin, RetrieveUpdateDestroyAPIView):
    """
    GET    /api/v1/admin/promotions/{id}/ — detalle
    PATCH  /api/v1/admin/promotions/{id}/ — editar (status: active|paused mapea a is_paused)
    DELETE /api/v1/admin/promotions/{id}/ — eliminar (409 si tiene canjes confirmados)
    """
    permission_classes = [IsStaffUser, HasPermission('promotions.manage')]
    http_method_names = ['get', 'patch', 'delete', 'head', 'options']

    def get_serializer_class(self):
        return PromotionWriteSerializer if self.request.method == 'PATCH' else PromotionSerializer

    def get_queryset(self) -> QuerySet[Promotion]:
        return _annotated_promotions()

    def perform_update(self, serializer: PromotionWriteSerializer) -> None:
        promotion = serializer.save()
        self.log_action(
            self.request, 'update', 'promotion', str(promotion.id),
            extra={'code': promotion.code, 'fields': sorted(self.request.data.keys())},
        )

    def destroy(self, request: Request, *args, **kwargs) -> Response:
        promotion: Promotion = self.get_object()
        # Cualquier canje bloquea el borrado (el FK es PROTECT): uno confirmado es
        # historial de facturación y uno pending es un pago en vuelo.
        if promotion.redemptions.exists():
            return Response(
                {'detail': 'La promoción tiene canjes registrados y no puede eliminarse. Puedes pausarla.'},
                status=status.HTTP_409_CONFLICT,
            )
        self.log_action(
            request, 'delete', 'promotion', str(promotion.id),
            extra={'code': promotion.code},
        )
        promotion.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class AdminPromotionStatsView(APIView):
    """GET /api/v1/admin/promotions/{id}/stats/ — métricas de canjes de una promoción."""
    permission_classes = [IsStaffUser, HasPermission('promotions.manage')]

    def get(self, request: Request, pk: str) -> Response:
        try:
            promotion = Promotion.objects.get(pk=pk)
        except Promotion.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        redemptions = PromotionRedemption.objects.filter(promotion=promotion)
        totals = redemptions.aggregate(
            total_redemptions=Count('id'),
            confirmed=Count('id', filter=Q(status='confirmed')),
            pending=Count('id', filter=Q(status='pending')),
            released=Count('id', filter=Q(status='released')),
            total_discount=Sum('discount_amount', filter=Q(status='confirmed')),
            total_revenue=Sum('final_amount', filter=Q(status='confirmed')),
        )
        by_plan = list(
            redemptions.filter(status='confirmed')
            .values('plan')
            .annotate(count=Count('id'))
            .order_by('-count')
        )
        return Response({
            'total_redemptions': totals['total_redemptions'],
            'confirmed': totals['confirmed'],
            'pending': totals['pending'],
            'released': totals['released'],
            'total_discount': float(totals['total_discount'] or 0),
            'total_revenue': float(totals['total_revenue'] or 0),
            'by_plan': by_plan,
        })
