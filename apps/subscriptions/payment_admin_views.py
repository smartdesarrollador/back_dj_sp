"""
Cola de comprobantes de pago manual para el Admin Panel: listado con filtros y
revisión (aprobar / rechazar). Requiere `is_staff=True`.

La configuración de cada método vive en `payment_method_views.py`, bajo el mismo
prefijo `/admin/payments/`.
"""
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import PaymentProof
from .payment_methods import PAYMENT_METHODS, charge_currency
from .services import activate_payment_proof

logger = logging.getLogger(__name__)
User = get_user_model()


def _serialize_proof(proof: PaymentProof) -> dict:
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
        'method':         proof.method,
        # Distingue «no hay conversión porque se pagó en dólares» de «no la hay porque
        # el comprobante es anterior al registro de tasa»: los dos llegan con
        # `amount_pen` a null y el panel los explicaría igual, que es engañoso.
        'charge_currency': charge_currency(proof.method),
        'transaction_reference': proof.transaction_reference,
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


# ── Admin proofs list ─────────────────────────────────────────────────────────

class ProofListView(APIView):
    """Listado paginado de comprobantes de pago, de todos los métodos. Solo staff."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not request.user.is_staff:
            return Response({'detail': 'Staff access required.'}, status=403)

        qs = PaymentProof.objects.select_related(
            'subscription__tenant', 'redemption__promotion'
        ).order_by('-created_at')

        # Filters
        proof_status = request.query_params.get('status', '').strip()
        plan         = request.query_params.get('plan', '').strip()
        method       = request.query_params.get('method', '').strip()
        date_from    = request.query_params.get('date_from', '').strip()
        date_to      = request.query_params.get('date_to', '').strip()

        if proof_status in ('pending', 'approved', 'rejected'):
            qs = qs.filter(status=proof_status)
        if plan in ('starter', 'professional', 'enterprise'):
            qs = qs.filter(plan=plan)
        # Una sola cola para todos los métodos, con filtro — no una pestaña por método:
        # lo que el revisor necesita saber es cuántos pagos esperan revisión, vengan
        # de donde vengan.
        if method in PAYMENT_METHODS:
            qs = qs.filter(method=method)
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
        all_proofs = PaymentProof.objects.all()
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

class ProofReviewView(APIView):
    """Aprueba o rechaza un comprobante de pago. Solo staff."""
    permission_classes = [IsAuthenticated]

    def patch(self, request, proof_id):
        if not request.user.is_staff:
            return Response({'detail': 'Staff access required.'}, status=403)

        new_status = request.data.get('status', '').strip()
        if new_status not in ('approved', 'rejected'):
            return Response({'detail': 'status must be "approved" or "rejected".'}, status=400)

        try:
            proof = PaymentProof.objects.select_related(
                'subscription__tenant'
            ).get(pk=proof_id)
        except PaymentProof.DoesNotExist:
            return Response({'detail': 'Proof not found.'}, status=404)

        if proof.status != 'pending':
            return Response({'detail': f'Proof is already {proof.status}.'}, status=400)

        tenant       = proof.subscription.tenant
        subscription = proof.subscription
        hub_url      = getattr(settings, 'FRONTEND_HUB_URL', '').rstrip('/')
        # El correo nombra el método por el que pagó de verdad: decirle «tu pago Yape»
        # a quien pagó por PayPal le hace dudar de si el mensaje es para él.
        method_label = proof.get_method_display()

        if new_status == 'approved':
            activate_payment_proof(proof)

            owner = tenant.users.order_by('created_at').first()
            if owner:
                send_mail(
                    subject='¡Tu cuenta ha sido activada!',
                    message=(
                        f"Hola {owner.name},\n\n"
                        f"Tu pago por {method_label} fue verificado exitosamente. "
                        f"Tu plan {proof.plan.capitalize()} ya está activo.\n\n"
                        f"Ingresa a tu cuenta: {hub_url}/login\n\n"
                        f"Saludos,\nEl equipo"
                    ),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[owner.email],
                    fail_silently=True,
                )
            logger.info('ProofReview: proof %s approved by staff %s', proof.id, request.user.email)

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
                    subject=f'Tu pago por {method_label} no pudo ser verificado',
                    message=(
                        f"Hola {owner.name},\n\n"
                        f"Lamentablemente no pudimos verificar tu comprobante de pago por {method_label} "
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
            logger.info('ProofReview: proof %s rejected by staff %s', proof.id, request.user.email)

        return Response(_serialize_proof(proof))
