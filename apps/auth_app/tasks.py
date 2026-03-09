from datetime import timedelta

from celery import shared_task
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
