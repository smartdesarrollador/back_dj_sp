"""
YapeUpgradeView — authenticated Yape payment proof for plan upgrades and renewals.

POST /api/v1/admin/subscriptions/yape-upgrade/

Used when a logged-in tenant wants to **mejorar** su plan o **renovar** el que ya
tiene, subiendo un comprobante de pago Yape. Unlike YapePaymentProofView (which uses
a Redis token for unauthenticated tenants right after registration), this endpoint
requires a valid JWT since the user is already logged in.

Renovación y upgrade son el mismo acto para el sistema —subir un comprobante que un
admin aprueba y que activa un período—, así que comparten endpoint y flujo de
aprobación (`activate_yape_proof`). Lo único que cambia es si el plan pagado es igual
o superior al actual. Ver ADR-008, decisión 3.
"""
import logging
import secrets

from django.db import transaction
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.promotions.models import PromotionRedemption
from apps.promotions.services import (
    BILLING_CYCLES,
    REASON_MESSAGES,
    compute_discount,
    find_valid_promotion,
    get_plan_price,
)
from apps.subscriptions.models import Subscription, YapePaymentProof
from apps.subscriptions.payment_methods import (
    accepts_proofs,
    charges_in_pen,
    requires_reference,
)
from apps.subscriptions.services import RENEWAL_WINDOW_DAYS, is_renewable
from apps.subscriptions.tasks import notify_yape_payment
from utils.currency import capture_pen_snapshot
from utils.uploads import validate_upload

logger = logging.getLogger(__name__)

VALID_PLANS = ('starter', 'professional', 'enterprise')
PLAN_ORDER = ('free', 'starter', 'professional', 'enterprise')


def _get_tenant(request):
    if hasattr(request, 'tenant') and request.tenant:
        return request.tenant
    return getattr(request.user, 'tenant', None)


class YapeUpgradeView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(
        tags=['admin-billing'],
        summary='Submit Yape payment proof for plan upgrade or renewal (authenticated)',
        responses={
            201: OpenApiResponse(description='Proof submitted, pending admin review'),
            400: OpenApiResponse(description='Validation error'),
            409: OpenApiResponse(description='A payment proof is already pending review'),
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

        billing_cycle = str(request.data.get('billing_cycle', 'monthly')).strip() or 'monthly'
        if billing_cycle not in BILLING_CYCLES:
            return Response(
                {'detail': 'Ciclo de facturación inválido. Debe ser monthly o annual.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        subscription, _ = Subscription.objects.get_or_create(
            tenant=tenant,
            defaults={'plan': tenant.plan, 'status': 'trialing'},
        )
        # El tenant ya está cargado: evita que is_renewable() vuelva a pedirlo.
        subscription.tenant = tenant

        # Un solo comprobante pendiente por tenant. Sin esto, dos submits seguidos
        # crean dos proofs que un admin podría aprobar ambos, activando (y cobrando)
        # dos veces — la ventana de doble aprobación de BACKLOG.md.
        pending = subscription.yape_proofs.filter(status='pending').first()
        if pending is not None:
            return Response(
                {
                    'detail': (
                        'Ya tienes un comprobante en revisión. Te avisaremos por email '
                        'en cuanto lo validemos.'
                    ),
                    'proof_id': str(pending.id),
                },
                status=status.HTTP_409_CONFLICT,
            )

        current_plan = tenant.plan
        is_renewal = plan == current_plan
        if is_renewal:
            if not is_renewable(subscription):
                return Response(
                    {'detail': (
                        f'Tu plan {current_plan} no está próximo a vencer. Podrás '
                        f'renovarlo durante los últimos {RENEWAL_WINDOW_DAYS} días '
                        f'del período.'
                    )},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        elif PLAN_ORDER.index(plan) < PLAN_ORDER.index(current_plan):
            return Response(
                {'detail': f'El plan {plan} no es un upgrade desde {current_plan}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Antes que la imagen, para no gastar el procesado en un request que se va a
        # rechazar. Rechazar métodos no habilitados impide que llegue un comprobante
        # que el resto del sistema aún no sabe tratar.
        method = str(request.data.get('method', 'yape')).strip() or 'yape'
        if not accepts_proofs(method):
            return Response(
                {'detail': 'Método de pago no disponible.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # La referencia es lo que hace verificable el pago contra el panel del proveedor.
        # Se exige aquí y no solo en el formulario del Hub: un guardarraíl que vive en el
        # cliente se salta llamando al endpoint directamente.
        transaction_reference = str(
            request.data.get('transaction_reference', '')
        ).strip()[:100]
        if requires_reference(method) and not transaction_reference:
            return Response(
                {'detail': 'Se requiere el ID de transacción del pago.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        screenshot = request.FILES.get('screenshot')
        if not screenshot:
            return Response(
                {'detail': 'Se requiere el comprobante (screenshot).'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        validate_upload(screenshot, category='payment_proof')

        # El monto SIEMPRE se calcula en servidor — nunca se confía en el del cliente.
        promo_code = str(request.data.get('promo_code', '')).strip()
        promotion = None
        if promo_code:
            promotion, reason = find_valid_promotion(promo_code, plan, tenant=tenant)
            if promotion is None:
                return Response(
                    {'detail': REASON_MESSAGES[reason], 'promo_reason': reason},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            amounts = compute_discount(promotion, plan, billing_cycle)
        else:
            price = get_plan_price(plan, billing_cycle)
            amounts = {'original': price, 'discount': price * 0, 'final': price}

        admin_token = secrets.token_urlsafe(48)
        # Foto de la tasa en el momento del pago: el cliente transfirió soles y la
        # aprobación puede tardar días, con la tasa moviéndose por medio. Solo aplica a
        # los métodos que cobran en soles — anotarle una conversión a un pago hecho en
        # dólares sería inventarle al comprobante un importe que nadie transfirió.
        if charges_in_pen(method):
            exchange_rate, amount_pen = capture_pen_snapshot(amounts['final'])
        else:
            exchange_rate, amount_pen = None, None
        with transaction.atomic():
            proof = YapePaymentProof.objects.create(
                subscription=subscription,
                method=method,
                screenshot=screenshot,
                transaction_reference=transaction_reference,
                plan=plan,
                billing_cycle=billing_cycle,
                amount=amounts['final'],
                exchange_rate=exchange_rate,
                amount_pen=amount_pen,
                admin_token=admin_token,
            )
            if promotion is not None:
                PromotionRedemption.objects.create(
                    promotion=promotion,
                    tenant=tenant,
                    yape_proof=proof,
                    plan=plan,
                    original_amount=amounts['original'],
                    discount_amount=amounts['discount'],
                    final_amount=amounts['final'],
                )

        try:
            notify_yape_payment.delay(str(proof.id))
        except Exception:
            logger.warning('YapeUpgradeView: could not enqueue notify_yape_payment for proof %s', proof.id)

        return Response(
            {
                'message': 'Comprobante recibido. Lo revisaremos pronto y te notificaremos por email.',
                'proof_id': str(proof.id),
                'is_renewal': is_renewal,
                'billing_cycle': billing_cycle,
            },
            status=status.HTTP_201_CREATED,
        )
