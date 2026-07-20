"""Endpoint público de validación de cupones (sin auth — el usuario aún se registra)."""
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from utils.throttles import CouponValidateRateThrottle

from .services import PAID_PLANS, compute_discount, find_valid_promotion


class PromotionValidateView(APIView):
    """
    POST /api/v1/public/promotions/validate/  { code, plan }

    Siempre responde 200 con { valid: bool, ... } (nunca 404: no filtra qué
    códigos existen). Rate-limited por IP contra fuerza bruta de códigos.
    Los chequeos por-tenant (new_customers_only, max_uses_per_customer) corren
    recién en el submit del comprobante, cuando el tenant es conocido.
    """
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [CouponValidateRateThrottle]

    def post(self, request: Request) -> Response:
        from apps.subscriptions.models import YapeConfig

        code = str(request.data.get('code', '')).strip()
        plan = str(request.data.get('plan', '')).strip()
        if plan not in PAID_PLANS:
            return Response({'detail': 'Invalid plan.'}, status=status.HTTP_400_BAD_REQUEST)

        promotion, reason = find_valid_promotion(code, plan)
        if promotion is None:
            return Response({'valid': False, 'reason': reason})

        amounts = compute_discount(promotion, plan)
        exchange_rate = YapeConfig.get().exchange_rate
        return Response({
            'valid': True,
            'code': promotion.code,
            'type': promotion.type,
            'value': float(promotion.value),
            'original_price': float(amounts['original']),
            'discount_amount': float(amounts['discount']),
            'final_price': float(amounts['final']),
            'exchange_rate': str(exchange_rate),
            'final_price_pen': float(amounts['final'] * exchange_rate),
        })
