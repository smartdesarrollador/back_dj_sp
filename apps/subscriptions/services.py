"""Servicios compartidos entre los distintos flujos de aprobación de pagos Yape."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.subscriptions.models import Invoice, YapePaymentProof

User = get_user_model()


def activate_yape_proof(proof: YapePaymentProof) -> Invoice:
    """
    Aprueba un YapePaymentProof: activa Subscription/Tenant y registra el Invoice pagado.
    Usado tanto por el panel admin (YapeProofReviewView) como por los links de
    un click enviados por Telegram (YapeActivateView) — ver LL-005/gap de Invoice.
    """
    subscription = proof.subscription
    tenant = subscription.tenant
    now = timezone.now()
    period_end = now + timedelta(days=30)

    with transaction.atomic():
        subscription.plan = proof.plan
        subscription.status = 'active'
        subscription.current_period_start = now
        subscription.current_period_end = period_end
        subscription.trial_start = None
        subscription.trial_end = None
        subscription.save(update_fields=[
            'plan', 'status', 'current_period_start', 'current_period_end',
            'trial_start', 'trial_end', 'updated_at',
        ])
        tenant.plan = proof.plan
        tenant.is_active = True
        tenant.save(update_fields=['plan', 'is_active', 'updated_at'])
        User.objects.filter(tenant=tenant).update(is_active=True)
        proof.status = 'approved'
        proof.reviewed_at = now
        proof.save(update_fields=['status', 'reviewed_at', 'updated_at'])

        invoice = Invoice.objects.create(
            tenant=tenant,
            stripe_invoice_id=f'yape_{proof.id}',
            amount_cents=int(proof.amount * 100),
            currency='usd',
            status='paid',
            period_start=now,
            period_end=period_end,
            invoice_date=now,
            paid_at=now,
        )
    return invoice
