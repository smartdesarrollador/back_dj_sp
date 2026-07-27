"""
Servicios compartidos entre los distintos flujos de aprobación de pagos manuales
(Yape, PayPal) y estado de vencimiento/renovación de una suscripción.

`get_renewal_state()` / `is_renewable()` son la **única** fuente del criterio de
renovación: los consumen tanto `PlanUpgradeView` (para aceptar o rechazar el pago
del plan actual) como `CurrentSubscriptionSerializer` (para que el Hub sepa si
mostrar el CTA "Renovar"). Si divergieran, el Hub ofrecería un botón que el backend
rechaza con 400, o al contrario.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.subscriptions.models import Invoice, Subscription, PaymentProof

User = get_user_model()

# Duración del período según el ciclo pagado. Las claves son los valores de
# Subscription.BILLING_CYCLE_CHOICES.
PERIOD_DAYS = {'monthly': 30, 'annual': 365}

# Días antes de `current_period_end` en que aparece la opción de renovar. Acotar la
# ventana evita acumulaciones raras (pagar cinco años de golpe) sin un tope artificial.
RENEWAL_WINDOW_DAYS = 15

# Días de acceso completo tras vencer un plan pagado, antes de degradar a Free. El pago
# manual y lo revisa una persona: sin gracia, un retraso de revisión cortaría
# el servicio a quien ya pagó. Ver ADR-008, decisión 2.
GRACE_DAYS = 7

# Hitos de aviso previo al vencimiento (días antes) y media-ventana de búsqueda, que
# absorbe el jitter del scheduler. La idempotencia NO depende de la ventana: se apoya en
# Subscription.renewal_reminders_sent.
REMINDER_MILESTONES = (7, 3, 1)
REMINDER_WINDOW_HOURS = 12

STATE_ACTIVE = 'active'
STATE_EXPIRING_SOON = 'expiring_soon'
STATE_GRACE = 'grace'
STATE_EXPIRED = 'expired'

RENEWABLE_STATES = (STATE_EXPIRING_SOON, STATE_GRACE)


def get_renewal_state(subscription: Subscription) -> str:
    """
    Estado de vencimiento derivado — no se persiste. Ver ADR-008.

      'grace'         plan de pago vencido, dentro del período de gracia
      'expiring_soon' plan de pago al que le quedan <= RENEWAL_WINDOW_DAYS días
      'expired'       ya degradado a Free tras haber pagado alguna vez
      'active'        todo lo demás (incluye Free que nunca pagó, y trials)

    `expired` exige una Invoice pagada a propósito: sin ese filtro, un tenant cuyo
    *trial* Professional venció —`expire_professional_trials` deja `plan='free'` y
    `StartTrialView` había fijado `current_period_end`— se reportaría como "tu plan
    venció" sin haber pagado nunca.
    """
    now = timezone.now()
    period_end = subscription.current_period_end
    plan = subscription.tenant.plan

    if plan == 'free':
        has_lapsed_period = period_end is not None and period_end <= now
        if has_lapsed_period and Invoice.objects.filter(
            tenant_id=subscription.tenant_id, status='paid'
        ).exists():
            return STATE_EXPIRED
        return STATE_ACTIVE

    if subscription.status == 'past_due':
        return STATE_GRACE

    if period_end is not None and period_end - now <= timedelta(days=RENEWAL_WINDOW_DAYS):
        return STATE_EXPIRING_SOON

    return STATE_ACTIVE


def is_renewable(subscription: Subscription) -> bool:
    """
    True si el tenant puede pagar HOY el plan que ya tiene. Un tenant ya degradado a
    Free no renueva: vuelve a contratar por el camino de upgrade normal.
    """
    if subscription.tenant.plan == 'free':
        return False
    return get_renewal_state(subscription) in RENEWABLE_STATES


def activate_subscription_plan(
    subscription: Subscription,
    plan: str,
    amount: Decimal,
    invoice_ref: str,
    billing_cycle: str = 'monthly',
    exchange_rate: Decimal | None = None,
    amount_pen: Decimal | None = None,
) -> Invoice:
    """
    Activa un plan de pago: Subscription/Tenant activos por el ciclo pagado (30 o
    365 días), usuarios reactivados e Invoice pagado. Núcleo compartido entre la
    aprobación de un comprobante de pago manual y la activación directa por cupón 100%
    (amount=0). Corre dentro de transaction.atomic() (la abre si no hay una activa).

    Si el período vigente aún no venció, el nuevo se **suma** a lo que quedaba: pagar
    anticipado (renovación o upgrade a mitad de período) no pierde días, y pagar
    después de vencer no regala el tiempo en que el servicio estuvo impago. Ver
    ADR-008, decisión 5.

    Pagar también limpia el estado de vencimiento (`grace_until`,
    `renewal_reminders_sent`) que consume la tarea de expiración, y revoca una
    cancelación pendiente (`cancel_at_period_end`).

    `exchange_rate`/`amount_pen` son el testigo histórico del cobro: se **reciben**
    del comprobante que originó el pago, no se consultan aquí. Consultar la tasa al
    activar sería el bug que estos campos existen para evitar: la aprobación ocurre
    días después del pago y la tasa puede haberse movido. `None` cuando no hubo
    conversión (cupón 100%, Stripe).

    Raises:
        ValueError: ciclo de facturación desconocido, o snapshot incompleto.
    """
    if billing_cycle not in PERIOD_DAYS:
        raise ValueError(f'Unknown billing cycle: {billing_cycle}')
    # Ambos o ninguno: una tasa sin importe (o al revés) es peor que no tener nada,
    # porque aparenta trazabilidad.
    if (exchange_rate is None) != (amount_pen is None):
        raise ValueError('exchange_rate y amount_pen deben venir juntos o ninguno.')

    tenant = subscription.tenant
    now = timezone.now()
    period_length = timedelta(days=PERIOD_DAYS[billing_cycle])
    current_end = subscription.current_period_end

    update_fields = [
        'plan', 'status', 'billing_cycle', 'current_period_end',
        'trial_start', 'trial_end', 'grace_until', 'renewal_reminders_sent',
        'cancel_at_period_end', 'updated_at',
    ]
    if current_end and current_end > now:
        # Extensión: el período vigente se alarga. `current_period_start` no se toca
        # —empezó cuando empezó—; la factura cubre solo el tramo recién comprado.
        period_end = current_end + period_length
        invoice_period_start = current_end
    else:
        period_end = now + period_length
        invoice_period_start = now
        subscription.current_period_start = now
        update_fields.append('current_period_start')

    with transaction.atomic():
        subscription.plan = plan
        subscription.status = 'active'
        subscription.billing_cycle = billing_cycle
        subscription.current_period_end = period_end
        subscription.trial_start = None
        subscription.trial_end = None
        subscription.grace_until = None
        subscription.renewal_reminders_sent = []
        # Pagar revoca una baja pedida: dejar el flag haría que la tarea de
        # expiración degradase al tenant al llegar el período, pese a haber pagado.
        subscription.cancel_at_period_end = False
        subscription.save(update_fields=update_fields)
        tenant.plan = plan
        tenant.is_active = True
        tenant.save(update_fields=['plan', 'is_active', 'updated_at'])
        User.objects.filter(tenant=tenant).update(is_active=True)

        invoice = Invoice.objects.create(
            tenant=tenant,
            stripe_invoice_id=invoice_ref,
            amount_cents=int(amount * 100),
            currency='usd',
            exchange_rate=exchange_rate,
            # `amount_pen` ya viene quantizado a 2 decimales desde
            # capture_pen_snapshot, así que el ×100 es exacto.
            amount_pen_cents=None if amount_pen is None else int(amount_pen * 100),
            status='paid',
            period_start=invoice_period_start,
            period_end=period_end,
            invoice_date=now,
            paid_at=now,
        )
    return invoice


def activate_payment_proof(proof: PaymentProof) -> Invoice:
    """
    Aprueba un PaymentProof: activa Subscription/Tenant, registra el
    Invoice pagado y confirma el canje de cupón si lo hay (incrementa
    current_uses con lock). Usado tanto por el panel admin
    (ProofReviewView) como por los links de un click enviados por
    Telegram (ProofActivateView) — ver LL-005/gap de Invoice.
    """
    from apps.promotions.services import confirm_redemption

    with transaction.atomic():
        invoice = activate_subscription_plan(
            proof.subscription, proof.plan,
            amount=proof.amount,
            invoice_ref=f'manual_{proof.id}',
            billing_cycle=proof.billing_cycle,
            # Se heredan del comprobante tal cual, aunque la tasa vigente HOY sea
            # otra: la factura debe reflejar lo que vio y pagó el cliente. Los
            # comprobantes anteriores al snapshot los traen a None, y la factura
            # queda igual — en vez de inventarle una conversión.
            exchange_rate=proof.exchange_rate,
            amount_pen=proof.amount_pen,
        )
        proof.status = 'approved'
        proof.reviewed_at = timezone.now()
        proof.save(update_fields=['status', 'reviewed_at', 'updated_at'])

        redemption = getattr(proof, 'redemption', None)
        if redemption is not None and redemption.status == 'pending':
            confirm_redemption(redemption)
    return invoice
