"""
AuditLog model — records security-relevant events (e.g. credential reveals).
"""
from django.conf import settings
from django.db import models

from core.models import BaseModel


class AuditLog(BaseModel):
    """
    Immutable audit trail entry. One row per security-relevant action.
    """
    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='audit_logs',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='audit_logs',
    )
    action = models.CharField(max_length=100)          # e.g. 'credentials.reveal'
    resource_type = models.CharField(max_length=100)   # e.g. 'ProjectItemField'
    resource_id = models.CharField(max_length=50, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    extra = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'audit_logs'
        ordering = ['-created_at']

    def __str__(self) -> str:
        return f'{self.action} by {self.user_id} on {self.resource_type}:{self.resource_id}'
