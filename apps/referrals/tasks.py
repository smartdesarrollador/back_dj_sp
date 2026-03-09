from datetime import timedelta

from celery import shared_task
from django.db.models import F
from django.utils import timezone


@shared_task(name='apps.referrals.tasks.activate_pending_referrals')
def activate_pending_referrals() -> dict:
    """
    Activa referidos pendientes cuyo tenant referido tiene suscripción activa
    y han pasado al menos 7 días desde la creación.
    Aplica credit_amount al balance del referrer.
    """
    from apps.referrals.models import Referral
    from apps.subscriptions.models import Subscription

    cutoff = timezone.now() - timedelta(days=7)

    pending = Referral.objects.filter(
        status='pending',
        created_at__lt=cutoff,
        referred__subscription__status='active',
    ).select_related('referrer', 'referred')

    activated_count = 0
    for referral in pending:
        referral.status = 'active'
        referral.activated_at = timezone.now()
        referral.save(update_fields=['status', 'activated_at', 'updated_at'])

        Subscription.objects.filter(tenant=referral.referrer).update(
            credit_balance=F('credit_balance') + referral.credit_amount,
        )
        activated_count += 1

    return {'activated': activated_count}
