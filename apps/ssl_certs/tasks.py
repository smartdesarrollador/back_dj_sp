"""
Celery tasks for SSL certificate expiry alerting.
"""
import logging

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task
def check_ssl_expiry_alerts():
    """
    Checks SSL certificates approaching expiry and marks alert flags as sent.
    Should be scheduled via Celery beat (daily).
    """
    from apps.ssl_certs.models import SSLCertificate

    today = timezone.now().date()
    for days, flag in [(30, 'alert_30_sent'), (7, 'alert_7_sent'), (1, 'alert_1_sent')]:
        target = today + timezone.timedelta(days=days)
        certs = SSLCertificate.objects.filter(valid_until=target, **{flag: False})
        for cert in certs:
            logger.warning(
                'ssl_expiry_alert domain=%s days_remaining=%d tenant=%s',
                cert.domain, days, cert.tenant.slug,
            )
            setattr(cert, flag, True)
            cert.save(update_fields=[flag, 'updated_at'])
