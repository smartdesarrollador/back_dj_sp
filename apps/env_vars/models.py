"""
EnvVariable model — encrypted environment variables per tenant/user.
"""
from django.conf import settings
from django.db import models

from core.models import BaseModel
from utils.encryption import encrypt_value


class EnvVariable(BaseModel):
    ENVIRONMENT_CHOICES = [
        ('development', 'Development'),
        ('staging', 'Staging'),
        ('production', 'Production'),
        ('all', 'All Environments'),
    ]

    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='env_variables',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='env_variables',
    )
    key = models.CharField(max_length=100)
    value = models.TextField()
    is_encrypted = models.BooleanField(default=False)
    environment = models.CharField(
        max_length=20, choices=ENVIRONMENT_CHOICES, default='all'
    )
    description = models.TextField(blank=True)

    class Meta:
        db_table = 'env_variables'
        unique_together = [('tenant', 'user', 'key', 'environment')]
        ordering = ['key']
        indexes = [
            models.Index(fields=['tenant', 'user', 'environment']),
        ]

    def save(self, *args, **kwargs):
        if not self.is_encrypted:
            self.value = encrypt_value(self.value)
            self.is_encrypted = True
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f'{self.key} [{self.environment}]'
