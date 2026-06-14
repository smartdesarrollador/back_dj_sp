"""Subscription-related Celery tasks."""
import logging

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

    payload = {
        'proof_id':    str(proof.id),
        'plan':        proof.plan,
        'amount':      str(proof.amount),
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
