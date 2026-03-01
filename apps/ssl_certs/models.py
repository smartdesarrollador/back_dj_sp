"""
SSLCertificate model — tracks SSL certificates with expiry status and alert flags.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import BaseModel


class SSLCertificate(BaseModel):
    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='ssl_certs',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='ssl_certs',
    )
    domain = models.CharField(max_length=255)
    issuer = models.CharField(max_length=255, blank=True)
    valid_from = models.DateField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)
    certificate_pem = models.TextField(blank=True)
    alert_30_sent = models.BooleanField(default=False)
    alert_7_sent = models.BooleanField(default=False)
    alert_1_sent = models.BooleanField(default=False)

    class Meta:
        db_table = 'ssl_certs'
        ordering = ['valid_until']
        indexes = [
            models.Index(fields=['tenant', 'user', 'domain']),
            models.Index(fields=['tenant', 'valid_until']),
        ]

    @property
    def days_until_expiry(self) -> int | None:
        if not self.valid_until:
            return None
        return (self.valid_until - timezone.now().date()).days

    @property
    def status(self) -> str:
        days = self.days_until_expiry
        if days is None:
            return 'valid'
        if days < 0:
            return 'expired'
        if days <= 30:
            return 'expiring'
        return 'valid'

    def __str__(self) -> str:
        return self.domain
