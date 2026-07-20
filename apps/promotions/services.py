"""
Lógica de canje de cupones — única fuente de verdad para validación y cálculo
de descuentos. Los montos SIEMPRE se calculan en servidor: nunca confiar en el
amount que envía el cliente (ver prd/features/promo-codes-registro.md).

Dirección de imports: subscriptions y auth_app importan de aquí, nunca al revés.
"""
import logging
from decimal import ROUND_HALF_UP, Decimal

from django.utils import timezone

from .models import Promotion, PromotionRedemption

logger = logging.getLogger(__name__)

PAID_PLANS = ('starter', 'professional', 'enterprise')

# Razones de rechazo de un cupón. 'invalid' es deliberadamente opaco: cubre
# inexistente, pausado y aún-no-vigente para no filtrar qué códigos existen.
REASON_INVALID = 'invalid'
REASON_EXPIRED = 'expired'
REASON_DEPLETED = 'depleted'
REASON_PLAN_NOT_APPLICABLE = 'plan_not_applicable'
REASON_NEW_CUSTOMERS_ONLY = 'new_customers_only'
REASON_CUSTOMER_LIMIT = 'customer_limit'

REASON_MESSAGES = {
    REASON_INVALID: 'El código no es válido.',
    REASON_EXPIRED: 'El código ha expirado.',
    REASON_DEPLETED: 'El código alcanzó su límite de usos.',
    REASON_PLAN_NOT_APPLICABLE: 'El código no aplica al plan seleccionado.',
    REASON_NEW_CUSTOMERS_ONLY: 'El código es solo para clientes nuevos.',
    REASON_CUSTOMER_LIMIT: 'Ya usaste este código el máximo de veces permitido.',
}


def get_plan_price(plan: str) -> Decimal:
    """Precio mensual USD del plan: fila Plan si existe, si no PLAN_CATALOG."""
    from apps.subscriptions.models import Plan
    from utils.plans import PLAN_CATALOG

    try:
        return Decimal(Plan.objects.get(id=plan).price_monthly)
    except Plan.DoesNotExist:
        for entry in PLAN_CATALOG:
            if entry['id'] == plan:
                return Decimal(entry['price_monthly'])
        raise ValueError(f'Unknown plan: {plan}') from None


def find_valid_promotion(code: str, plan: str, tenant=None) -> tuple[Promotion | None, str | None]:
    """
    Valida un cupón en el orden del PRD. Devuelve (promotion, None) si es
    canjeable o (None, reason) si no. Los chequeos por-tenant
    (new_customers_only, max_uses_per_customer) solo corren si se pasa tenant
    (es decir, en el submit — el validate público no conoce al tenant).
    """
    normalized = (code or '').strip().upper()
    if not normalized:
        return None, REASON_INVALID

    try:
        promotion = Promotion.objects.get(code=normalized)
    except Promotion.DoesNotExist:
        return None, REASON_INVALID

    now = timezone.now()
    if promotion.is_paused or now < promotion.starts_at:
        return None, REASON_INVALID  # opaco: no revelar que el código existe
    if now > promotion.expires_at:
        return None, REASON_EXPIRED
    if promotion.max_uses is not None and promotion.current_uses >= promotion.max_uses:
        return None, REASON_DEPLETED
    if plan not in promotion.applicable_plans:
        return None, REASON_PLAN_NOT_APPLICABLE

    if tenant is not None:
        if promotion.new_customers_only and _tenant_has_paid_history(tenant):
            return None, REASON_NEW_CUSTOMERS_ONLY
        tenant_uses = PromotionRedemption.objects.filter(
            promotion=promotion, tenant=tenant, status__in=['pending', 'confirmed'],
        ).count()
        if tenant_uses >= promotion.max_uses_per_customer:
            return None, REASON_CUSTOMER_LIMIT

    return promotion, None


def _tenant_has_paid_history(tenant) -> bool:
    from apps.subscriptions.models import Invoice

    if PromotionRedemption.objects.filter(tenant=tenant, status='confirmed').exists():
        return True
    return Invoice.objects.filter(tenant=tenant, status='paid', amount_cents__gt=0).exists()


def compute_discount(promotion: Promotion, plan: str) -> dict:
    """{'original', 'discount', 'final'} en Decimal (2 decimales, HALF_UP)."""
    original = get_plan_price(plan)
    if promotion.type == 'percentage':
        discount = original * promotion.value / Decimal('100')
        if promotion.max_discount is not None:
            discount = min(discount, promotion.max_discount)
    else:  # fixed_amount
        discount = min(promotion.value, original)

    cents = Decimal('0.01')
    original = original.quantize(cents, rounding=ROUND_HALF_UP)
    discount = discount.quantize(cents, rounding=ROUND_HALF_UP)
    return {'original': original, 'discount': discount, 'final': original - discount}


def confirm_redemption(redemption: PromotionRedemption) -> None:
    """
    Confirma un canje al aprobarse el pago. LLAMAR DENTRO de una transacción:
    toma lock de la promoción para incrementar current_uses sin carreras.
    Si el cupo ya se alcanzó (carrera entre dos aprobaciones) procede
    igualmente — el cliente ya pagó; el cupo controla emisión, no aprobación.
    """
    now = timezone.now()
    promotion = Promotion.objects.select_for_update().get(pk=redemption.promotion_id)
    if promotion.max_uses is not None and promotion.current_uses >= promotion.max_uses:
        logger.warning(
            'confirm_redemption: promotion %s over max_uses (%s) — confirming anyway '
            '(payment already made)', promotion.code, promotion.max_uses,
        )
    promotion.current_uses += 1
    promotion.last_used_at = now
    promotion.save(update_fields=['current_uses', 'last_used_at', 'updated_at'])

    redemption.status = 'confirmed'
    redemption.confirmed_at = now
    redemption.save(update_fields=['status', 'confirmed_at', 'updated_at'])


def release_redemption_for_proof(proof) -> None:
    """Libera el canje pending de un comprobante rechazado (no consume el cupo)."""
    redemption = getattr(proof, 'redemption', None)
    if redemption is None or redemption.status != 'pending':
        return
    redemption.status = 'released'
    redemption.save(update_fields=['status', 'updated_at'])
