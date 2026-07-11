"""
YapeUpgradeView — authenticated Yape payment proof for plan upgrades.

POST /api/v1/admin/subscriptions/yape-upgrade/

Used when a logged-in tenant wants to upgrade their plan by submitting a Yape
payment screenshot. Unlike YapePaymentProofView (which uses a Redis token for
unauthenticated tenants right after registration), this endpoint requires a
valid JWT since the user is already logged in.
"""
import logging
import secrets
from decimal import Decimal, InvalidOperation

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.subscriptions.models import Subscription, YapePaymentProof
from apps.subscriptions.tasks import notify_yape_payment

logger = logging.getLogger(__name__)

VALID_PLANS = ('starter', 'professional', 'enterprise')
PLAN_ORDER = ('free', 'starter', 'professional', 'enterprise')

PLAN_PRICES_USD: dict[str, int] = {
    'starter': 29,
    'professional': 79,
    'enterprise': 199,
}


def _get_tenant(request):
    if hasattr(request, 'tenant') and request.tenant:
        return request.tenant
    return getattr(request.user, 'tenant', None)


class YapeUpgradeView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(
        tags=['admin-billing'],
        summary='Submit Yape payment proof for plan upgrade (authenticated)',
        responses={
            201: OpenApiResponse(description='Proof submitted, pending admin review'),
            400: OpenApiResponse(description='Validation error'),
        },
    )
    def post(self, request) -> Response:
        tenant = _get_tenant(request)
        if not tenant:
            return Response(
                {'detail': 'Tenant not found.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        plan = request.data.get('plan', '').strip()
        if plan not in VALID_PLANS:
            return Response(
                {'detail': 'Plan inválido. Debe ser starter, professional o enterprise.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        current_plan = tenant.plan
        if PLAN_ORDER.index(plan) <= PLAN_ORDER.index(current_plan):
            return Response(
                {'detail': f'El plan {plan} no es un upgrade desde {current_plan}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        screenshot = request.FILES.get('screenshot')
        if not screenshot:
            return Response(
                {'detail': 'Se requiere el comprobante (screenshot).'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        raw_amount = request.data.get('amount', str(PLAN_PRICES_USD.get(plan, 0)))
        try:
            amount = Decimal(raw_amount)
        except InvalidOperation:
            return Response({'detail': 'Monto inválido.'}, status=status.HTTP_400_BAD_REQUEST)

        subscription, _ = Subscription.objects.get_or_create(
            tenant=tenant,
            defaults={'plan': tenant.plan, 'status': 'trialing'},
        )

        admin_token = secrets.token_urlsafe(48)
        proof = YapePaymentProof.objects.create(
            subscription=subscription,
            screenshot=screenshot,
            plan=plan,
            amount=amount,
            admin_token=admin_token,
        )

        try:
            notify_yape_payment.delay(str(proof.id))
        except Exception:
            logger.warning('YapeUpgradeView: could not enqueue notify_yape_payment for proof %s', proof.id)

        return Response(
            {
                'message': 'Comprobante recibido. Lo revisaremos pronto y te notificaremos por email.',
                'proof_id': str(proof.id),
            },
            status=status.HTTP_201_CREATED,
        )
