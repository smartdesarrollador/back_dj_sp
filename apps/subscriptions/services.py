"""Servicios compartidos entre los distintos flujos de aprobación de pagos Yape."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.subscriptions.models import Invoice, Subscription, YapePaymentProof

User = get_user_model()


def activate_subscription_plan(
    subscription: Subscription,
    plan: str,
    amount: Decimal,
    invoice_ref: str,
) -> Invoice:
    """
    Activa un plan de pago: Subscription/Tenant activos por 30 días, usuarios
    reactivados e Invoice pagado. Núcleo compartido entre la aprobación de un
    comprobante Yape y la activación directa por cupón 100% (amount=0).
    Corre dentro de transaction.atomic() (la abre si no hay una activa).
    """
    tenant = subscription.tenant
    now = timezone.now()
    period_end = now + timedelta(days=30)

    with transaction.atomic():
        subscription.plan = plan
        subscription.status = 'active'
        subscription.current_period_start = now
        subscription.current_period_end = period_end
        subscription.trial_start = None
        subscription.trial_end = None
        subscription.save(update_fields=[
            'plan', 'status', 'current_period_start', 'current_period_end',
            'trial_start', 'trial_end', 'updated_at',
        ])
        tenant.plan = plan
        tenant.is_active = True
        tenant.save(update_fields=['plan', 'is_active', 'updated_at'])
        User.objects.filter(tenant=tenant).update(is_active=True)

        invoice = Invoice.objects.create(
            tenant=tenant,
            stripe_invoice_id=invoice_ref,
            amount_cents=int(amount * 100),
            currency='usd',
            status='paid',
            period_start=now,
            period_end=period_end,
            invoice_date=now,
            paid_at=now,
        )
    return invoice


def activate_yape_proof(proof: YapePaymentProof) -> Invoice:
    """
    Aprueba un YapePaymentProof: activa Subscription/Tenant, registra el
    Invoice pagado y confirma el canje de cupón si lo hay (incrementa
    current_uses con lock). Usado tanto por el panel admin
    (YapeProofReviewView) como por los links de un click enviados por
    Telegram (YapeActivateView) — ver LL-005/gap de Invoice.
    """
    from apps.promotions.services import confirm_redemption

    with transaction.atomic():
        invoice = activate_subscription_plan(
            proof.subscription, proof.plan,
            amount=proof.amount,
            invoice_ref=f'yape_{proof.id}',
        )
        proof.status = 'approved'
        proof.reviewed_at = timezone.now()
        proof.save(update_fields=['status', 'reviewed_at', 'updated_at'])

        redemption = getattr(proof, 'redemption', None)
        if redemption is not None and redemption.status == 'pending':
            confirm_redemption(redemption)
    return invoice
