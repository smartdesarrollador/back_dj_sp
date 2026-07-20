"""Subscription-related Celery tasks."""
import logging
from datetime import timedelta

import requests
from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(
    name='apps.subscriptions.tasks.notify_yape_payment',
    ignore_result=True,
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=10,
)
def notify_yape_payment(proof_id: str) -> None:
    """
    POST to the n8n webhook with Yape proof data so n8n can:
    1. Analyze the screenshot with OpenAI vision
    2. Send a Telegram message with approve/reject one-click links
    """
    from apps.subscriptions.models import YapePaymentProof

    try:
        proof = YapePaymentProof.objects.select_related(
            'subscription__tenant'
        ).get(pk=proof_id)
    except YapePaymentProof.DoesNotExist:
        logger.error('notify_yape_payment: YapePaymentProof %s not found', proof_id)
        return

    webhook_url = getattr(settings, 'N8N_YAPE_PAYMENT_WEBHOOK_URL', '')
    if not webhook_url:
        logger.warning('notify_yape_payment: N8N_YAPE_PAYMENT_WEBHOOK_URL not configured')
        return

    tenant = proof.subscription.tenant
    owner  = tenant.users.order_by('created_at').first()
    base_url = getattr(settings, 'APP_BASE_URL', '').rstrip('/')

    redemption = getattr(proof, 'redemption', None)
    promo = None
    if redemption is not None:
        promo = {
            'code':            redemption.promotion.code,
            'original_amount': str(redemption.original_amount),
            'discount_amount': str(redemption.discount_amount),
            'final_amount':    str(redemption.final_amount),
        }

    payload = {
        'proof_id':    str(proof.id),
        'plan':        proof.plan,
        'amount':      str(proof.amount),
        'promo':       promo,
        'tenant': {
            'id':        str(tenant.id),
            'name':      tenant.name,
            'slug':      tenant.slug,
            'subdomain': tenant.subdomain,
        },
        'user': {
            'name':  owner.name  if owner else '',
            'email': owner.email if owner else '',
        },
        'image_url':   f"{base_url}/media/{proof.screenshot.name}",
        'approve_url': f"{base_url}/api/v1/public/yape-payment/activate/{proof.admin_token}/",
        'reject_url':  f"{base_url}/api/v1/public/yape-payment/reject/{proof.admin_token}/",
        'submitted_at': timezone.now().isoformat(),
    }

    response = requests.post(webhook_url, json=payload, timeout=30)
    response.raise_for_status()
    logger.info(
        'notify_yape_payment: proof %s sent to n8n (status=%s)',
        proof_id, response.status_code,
    )


@shared_task(
    name='apps.subscriptions.tasks.expire_professional_trials',
    ignore_result=True,
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=10,
)
def expire_professional_trials() -> None:
    """
    Downgrade all Professional trialing subscriptions whose trial has expired.
    Sends downgrade notification email to each tenant owner.
    Runs daily at 04:00 UTC via Celery beat.
    """
    from apps.subscriptions.models import Subscription
    from django.core.mail import send_mail
    from django.db import transaction

    now = timezone.now()
    expired_subs = Subscription.objects.filter(
        plan='professional',
        status='trialing',
        trial_end__lte=now,
    ).select_related('tenant')

    for sub in expired_subs:
        tenant = sub.tenant
        owner = tenant.users.order_by('created_at').first()

        with transaction.atomic():
            sub.plan = 'free'
            sub.status = 'active'
            sub.trial_start = None
            sub.trial_end = None
            sub.save(update_fields=['plan', 'status', 'trial_start', 'trial_end', 'updated_at'])
            tenant.plan = 'free'
            tenant.save(update_fields=['plan', 'updated_at'])

        if owner:
            hub_url = getattr(settings, 'FRONTEND_HUB_URL', '').rstrip('/')
            send_mail(
                subject='Tu período de prueba Professional ha finalizado',
                message=(
                    f'Hola {owner.name},\n\n'
                    'Tu prueba gratuita de 30 días del Plan Professional ha finalizado. '
                    'Tu cuenta ha vuelto al Plan Free.\n\n'
                    f'Si deseas continuar con Professional, accede a tu panel y actualiza tu plan: '
                    f'{hub_url}/subscription\n\n'
                    '— El equipo de Hub de Servicios'
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[owner.email],
                fail_silently=True,
            )
        logger.info('expire_professional_trials: downgraded tenant %s', tenant.slug)


@shared_task(
    name='apps.subscriptions.tasks.remind_professional_trial_expiry',
    ignore_result=True,
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=10,
)
def remind_professional_trial_expiry() -> None:
    """
    Send a 7-day reminder email to tenants whose Professional trial expires in ~7 days.
    Uses a ±1 day window (6–8 days) to handle Celery beat scheduling jitter.
    Runs daily at 10:00 UTC via Celery beat.
    """
    from apps.subscriptions.models import Subscription
    from django.core.mail import send_mail

    now = timezone.now()
    window_start = now + timedelta(days=6)
    window_end = now + timedelta(days=8)

    reminder_subs = Subscription.objects.filter(
        plan='professional',
        status='trialing',
        trial_end__gte=window_start,
        trial_end__lte=window_end,
    ).select_related('tenant')

    for sub in reminder_subs:
        tenant = sub.tenant
        owner = tenant.users.order_by('created_at').first()
        if not owner:
            continue

        days_left = max(1, (sub.trial_end - now).days)
        hub_url = getattr(settings, 'FRONTEND_HUB_URL', '').rstrip('/')
        send_mail(
            subject=f'Tu prueba Professional termina en {days_left} días',
            message=(
                f'Hola {owner.name},\n\n'
                f'Tu prueba gratuita del Plan Professional termina en {days_left} días '
                f'(el {sub.trial_end.strftime("%d/%m/%Y")}).\n\n'
                'Para no perder el acceso a funcionalidades profesionales, actualiza '
                f'tu plan antes de que expire: {hub_url}/subscription\n\n'
                '— El equipo de Hub de Servicios'
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[owner.email],
            fail_silently=True,
        )
        logger.info('remind_professional_trial_expiry: reminded tenant %s', tenant.slug)
