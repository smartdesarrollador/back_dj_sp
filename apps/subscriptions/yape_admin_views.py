"""
Admin API endpoints for Yape payment configuration and proof management.
Requires is_staff=True. Staff can configure Yape settings and review payment proofs.
"""
import logging
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.mixins import AuditMixin
from utils.currency import get_legacy_exchange_rate_str

from .models import CurrencyConfig, YapeConfig, YapePaymentProof
from .serializers import CurrencyConfigUpdateSerializer
from .services import activate_yape_proof

logger = logging.getLogger(__name__)
User = get_user_model()


def _serialize_config(cfg: YapeConfig) -> dict:
    return {
        'phone':             cfg.phone,
        'holder_name':       cfg.holder_name,
        'is_enabled':        cfg.is_enabled,
        # La fuente de verdad es CurrencyConfig; cfg.exchange_rate es una sombra
        # dual-escrita que se elimina cuando el Admin migre al endpoint de moneda.
        'exchange_rate':     get_legacy_exchange_rate_str(),
        'instructions_note': cfg.instructions_note,
        'updated_at':        cfg.updated_at.isoformat() if cfg.updated_at else None,
    }


def _serialize_proof(proof: YapePaymentProof) -> dict:
    base_url = getattr(settings, 'APP_BASE_URL', '').rstrip('/')
    tenant   = proof.subscription.tenant
    owner    = tenant.users.order_by('created_at').first()

    # Canje de cupón asociado (reverse OneToOne: AttributeError si no existe)
    redemption = getattr(proof, 'redemption', None)
    promo = None
    if redemption is not None:
        promo = {
            'code':            redemption.promotion.code,
            'original_amount': str(redemption.original_amount),
            'discount_amount': str(redemption.discount_amount),
            'final_amount':    str(redemption.final_amount),
        }

    return {
        'id':             str(proof.id),
        'screenshot_url': f"{base_url}/media/{proof.screenshot.name}" if proof.screenshot else '',
        'plan':           proof.plan,
        # Sin el ciclo, el revisor no distingue un pago anual legítimo de un importe
        # anómalo — y al aprobar activa 30 o 365 días según cuál sea.
        'billing_cycle':  proof.billing_cycle,
        'amount':         str(proof.amount),
        # Soles que el cliente transfirió de verdad, con la tasa de ESE momento —
        # es contra lo que el revisor compara el screenshot. `None` en comprobantes
        # anteriores al snapshot: el panel dice "sin conversión registrada" en vez
        # de recalcular con la tasa de hoy, que es lo que hace irreconstruible el
        # descuadre.
        'exchange_rate':  str(proof.exchange_rate) if proof.exchange_rate is not None else None,
        'amount_pen':     str(proof.amount_pen) if proof.amount_pen is not None else None,
        'promo':          promo,
        'status':         proof.status,
        'tenant_name':    tenant.name,
        'tenant_email':   owner.email if owner else '',
        'tenant_slug':    tenant.slug,
        'created_at':     proof.created_at.isoformat(),
        'reviewed_at':    proof.reviewed_at.isoformat() if proof.reviewed_at else None,
    }


# ── Public config endpoint (no auth) ─────────────────────────────────────────

class YapeConfigPublicView(APIView):
    """Public endpoint — Hub reads this to display Yape payment instructions."""
    permission_classes     = [AllowAny]
    authentication_classes = []

    def get(self, request):
        # Reutiliza _serialize_config en vez de duplicar el dict: eran dos copias
        # del mismo contrato y solo una leía la fuente de verdad nueva.
        # `updated_at` se descarta a propósito — el contrato público son 5 claves
        # y ampliarlo aquí no aporta nada al Hub.
        data = _serialize_config(YapeConfig.get())
        data.pop('updated_at', None)
        return Response(data)


# ── Admin config endpoint ─────────────────────────────────────────────────────

class YapeConfigView(AuditMixin, APIView):
    """
    GET/PATCH Yape configuration. Staff only.

    `exchange_rate` ya no se guarda aquí: su fuente de verdad es CurrencyConfig y
    este endpoint actúa de fachada mientras el Admin no migre a
    /admin/billing/currency/. Se valida con el mismo serializer que el endpoint
    nuevo — un tipo de cambio mal tecleado multiplica todos los precios que ve el
    cliente, así que no puede entrar por la puerta de atrás sin validar.
    """
    permission_classes = [IsAuthenticated]

    def _check_staff(self, request):
        if not request.user.is_staff:
            return Response({'detail': 'Staff access required.'}, status=403)
        return None

    def get(self, request):
        if err := self._check_staff(request):
            return err
        return Response(_serialize_config(YapeConfig.get()))

    def patch(self, request):
        if err := self._check_staff(request):
            return err
        cfg = YapeConfig.get()
        allowed = {'phone', 'holder_name', 'is_enabled', 'instructions_note'}
        for field, value in request.data.items():
            if field in allowed:
                setattr(cfg, field, value)

        # El tipo de cambio se redirige a CurrencyConfig, validado. El orden
        # importa: currency.save() invalida la caché ANTES de que
        # _serialize_config la vuelva a leer, así el PATCH devuelve el valor nuevo.
        if 'exchange_rate' in request.data:
            serializer = CurrencyConfigUpdateSerializer(
                data={'usd_to_pen': request.data['exchange_rate']}, partial=True
            )
            if not serializer.is_valid():
                # Re-etiquetado: este endpoint habla 'exchange_rate'. Devolver el
                # error bajo 'usd_to_pen' obligaría al form del Admin a conocer el
                # nombre interno del modelo nuevo.
                raise ValidationError(
                    {'exchange_rate': serializer.errors.get('usd_to_pen', ['Valor inválido.'])}
                )
            currency = CurrencyConfig.get()
            before = currency.usd_to_pen
            currency.usd_to_pen = serializer.validated_data['usd_to_pen']
            currency.source     = 'manual'
            currency.updated_by = request.user
            currency.save()
            cfg.exchange_rate = currency.usd_to_pen.quantize(Decimal('0.01'))  # sombra
            self.log_action(
                request, 'update', 'currency_config', '1',
                extra={
                    'usd_to_pen_before': str(before),
                    'usd_to_pen_after':  str(currency.usd_to_pen),
                    'via': 'legacy_yape_endpoint',
                },
            )

        cfg.save()
        return Response(_serialize_config(cfg))


# ── Admin proofs list ─────────────────────────────────────────────────────────

class YapeProofListView(APIView):
    """GET paginated list of Yape payment proofs. Staff only."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not request.user.is_staff:
            return Response({'detail': 'Staff access required.'}, status=403)

        qs = YapePaymentProof.objects.select_related(
            'subscription__tenant', 'redemption__promotion'
        ).order_by('-created_at')

        # Filters
        proof_status = request.query_params.get('status', '').strip()
        plan         = request.query_params.get('plan', '').strip()
        date_from    = request.query_params.get('date_from', '').strip()
        date_to      = request.query_params.get('date_to', '').strip()

        if proof_status in ('pending', 'approved', 'rejected'):
            qs = qs.filter(status=proof_status)
        if plan in ('starter', 'professional', 'enterprise'):
            qs = qs.filter(plan=plan)
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)

        # Pagination
        try:
            page     = max(1, int(request.query_params.get('page', 1)))
            per_page = min(50, max(1, int(request.query_params.get('per_page', 5))))
        except (ValueError, TypeError):
            page, per_page = 1, 5

        total   = qs.count()
        offset  = (page - 1) * per_page
        proofs  = qs[offset: offset + per_page]

        # KPI counts (unfiltered by date/plan but respecting current filters for totals)
        all_proofs = YapePaymentProof.objects.all()
        kpi = {
            'total':    all_proofs.count(),
            'pending':  all_proofs.filter(status='pending').count(),
            'approved': all_proofs.filter(status='approved').count(),
            'rejected': all_proofs.filter(status='rejected').count(),
        }

        return Response({
            'proofs':     [_serialize_proof(p) for p in proofs],
            'kpi':        kpi,
            'pagination': {
                'page':        page,
                'per_page':    per_page,
                'total':       total,
                'total_pages': max(1, -(-total // per_page)),  # ceiling division
            },
        })


# ── Admin proof review (approve / reject) ────────────────────────────────────

class YapeProofReviewView(APIView):
    """PATCH to approve or reject a Yape payment proof. Staff only."""
    permission_classes = [IsAuthenticated]

    def patch(self, request, proof_id):
        if not request.user.is_staff:
            return Response({'detail': 'Staff access required.'}, status=403)

        new_status = request.data.get('status', '').strip()
        if new_status not in ('approved', 'rejected'):
            return Response({'detail': 'status must be "approved" or "rejected".'}, status=400)

        try:
            proof = YapePaymentProof.objects.select_related(
                'subscription__tenant'
            ).get(pk=proof_id)
        except YapePaymentProof.DoesNotExist:
            return Response({'detail': 'Proof not found.'}, status=404)

        if proof.status != 'pending':
            return Response({'detail': f'Proof is already {proof.status}.'}, status=400)

        tenant       = proof.subscription.tenant
        subscription = proof.subscription
        hub_url      = getattr(settings, 'FRONTEND_HUB_URL', '').rstrip('/')

        if new_status == 'approved':
            activate_yape_proof(proof)

            owner = tenant.users.order_by('created_at').first()
            if owner:
                send_mail(
                    subject='¡Tu cuenta ha sido activada!',
                    message=(
                        f"Hola {owner.name},\n\n"
                        f"Tu pago Yape fue verificado exitosamente. "
                        f"Tu plan {proof.plan.capitalize()} ya está activo.\n\n"
                        f"Ingresa a tu cuenta: {hub_url}/login\n\n"
                        f"Saludos,\nEl equipo"
                    ),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[owner.email],
                    fail_silently=True,
                )
            logger.info('YapeReview: proof %s approved by staff %s', proof.id, request.user.email)

        else:  # rejected
            from apps.promotions.services import release_redemption_for_proof

            with transaction.atomic():
                subscription.plan   = 'free'
                subscription.status = 'active'
                subscription.save(update_fields=['plan', 'status', 'updated_at'])
                tenant.plan = 'free'
                tenant.save(update_fields=['plan', 'updated_at'])
                proof.status      = 'rejected'
                proof.reviewed_at = timezone.now()
                proof.save(update_fields=['status', 'reviewed_at', 'updated_at'])
                release_redemption_for_proof(proof)

            owner = tenant.users.order_by('created_at').first()
            if owner:
                send_mail(
                    subject='Tu pago Yape no pudo ser verificado',
                    message=(
                        f"Hola {owner.name},\n\n"
                        f"Lamentablemente no pudimos verificar tu comprobante de pago Yape "
                        f"para el plan {proof.plan.capitalize()}.\n\n"
                        f"Tu cuenta continúa activa con el plan Free. "
                        f"Si deseas intentarlo de nuevo o tienes dudas, contáctanos respondiendo este email.\n\n"
                        f"Ingresa a tu cuenta: {hub_url}/login\n\n"
                        f"Saludos,\nEl equipo"
                    ),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[owner.email],
                    fail_silently=True,
                )
            logger.info('YapeReview: proof %s rejected by staff %s', proof.id, request.user.email)

        return Response(_serialize_proof(proof))
