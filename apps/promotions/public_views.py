"""Endpoint público de validación de cupones (sin auth — el usuario aún se registra)."""
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

# Import a nivel de módulo: utils.currency difiere internamente el import del
# modelo, así que promotions no importa apps.subscriptions y se respeta la
# dirección de imports que declara services.py.
from utils.currency import get_exchange_rate
from utils.throttles import CouponValidateRateThrottle

from .services import BILLING_CYCLES, PAID_PLANS, compute_discount, find_valid_promotion


class PromotionValidateView(APIView):
    """
    POST /api/v1/public/promotions/validate/  { code, plan, billing_cycle? }

    Siempre responde 200 con { valid: bool, ... } (nunca 404: no filtra qué
    códigos existen). Rate-limited por IP contra fuerza bruta de códigos.
    Los chequeos por-tenant (new_customers_only, max_uses_per_customer) corren
    recién en el submit del comprobante, cuando el tenant es conocido.

    `billing_cycle` es opcional (default 'monthly'): determina sobre qué precio
    del plan se calcula el descuento. Un plan inválido o un ciclo inválido sí
    devuelven 400 — son errores del cliente, no un cupón rechazado.
    """
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [CouponValidateRateThrottle]

    def post(self, request: Request) -> Response:
        code = str(request.data.get('code', '')).strip()
        plan = str(request.data.get('plan', '')).strip()
        if plan not in PAID_PLANS:
            return Response({'detail': 'Invalid plan.'}, status=status.HTTP_400_BAD_REQUEST)

        billing_cycle = str(request.data.get('billing_cycle', 'monthly')).strip() or 'monthly'
        if billing_cycle not in BILLING_CYCLES:
            return Response(
                {'detail': 'Invalid billing cycle.'}, status=status.HTTP_400_BAD_REQUEST
            )

        promotion, reason = find_valid_promotion(code, plan)
        if promotion is None:
            return Response({'valid': False, 'reason': reason})

        amounts = compute_discount(promotion, plan, billing_cycle)
        # La tasa completa, la misma con la que se captura el testigo del cobro: si
        # aquí se redondeara, el cliente vería un importe distinto del que el servidor
        # guarda — justo el descuadre que el snapshot existe para evitar.
        exchange_rate = get_exchange_rate('PEN')
        return Response({
            'valid': True,
            'code': promotion.code,
            'type': promotion.type,
            'value': float(promotion.value),
            'billing_cycle': billing_cycle,
            'original_price': float(amounts['original']),
            'discount_amount': float(amounts['discount']),
            'final_price': float(amounts['final']),
            'exchange_rate': str(exchange_rate),
            'final_price_pen': float(amounts['final'] * exchange_rate),
        })
