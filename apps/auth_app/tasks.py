from datetime import timedelta

import requests
from celery import shared_task
from django.conf import settings
from django.utils import timezone


@shared_task(name='apps.auth_app.tasks.cleanup_expired_sso_tokens')
def cleanup_expired_sso_tokens() -> dict:
    from apps.auth_app.models import SSOToken

    now = timezone.now()
    expired, _ = SSOToken.objects.filter(
        expires_at__lt=now,
        used_at__isnull=True,
    ).delete()
    used_old, _ = SSOToken.objects.filter(
        used_at__lt=now - timedelta(hours=1),
    ).delete()
    return {'expired_unused_deleted': expired, 'used_old_deleted': used_old}


@shared_task(
    name='apps.auth_app.tasks.notify_n8n_nuevo_registro',
    ignore_result=True,
    autoretry_for=(Exception,),
    max_retries=2,
    retry_backoff=5,
)
def notify_n8n_nuevo_registro(user_data: dict, tenant_data: dict, plan: str) -> None:
    url = getattr(settings, 'N8N_WEBHOOK_REGISTRO_URL', '')
    if not url:
        return
    payload = {
        'event': 'tenant.registered',
        'user': user_data,
        'tenant': tenant_data,
        'plan': plan,
        'timestamp': timezone.now().isoformat(),
    }
    requests.post(url, json=payload, timeout=5)
