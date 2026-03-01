"""
SSHKey model — stores SSH key pairs with auto-encrypted private key and auto-calculated fingerprint.
"""
import base64
import hashlib

from django.conf import settings
from django.db import models

from core.models import BaseModel
from utils.encryption import encrypt_value


class SSHKey(BaseModel):
    ALGORITHM_CHOICES = [
        ('rsa', 'RSA'),
        ('ed25519', 'Ed25519'),
        ('ecdsa', 'ECDSA'),
        ('dsa', 'DSA'),
    ]

    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='ssh_keys',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='ssh_keys',
    )
    name = models.CharField(max_length=255)
    public_key = models.TextField()
    private_key = models.TextField(blank=True)
    is_encrypted = models.BooleanField(default=False)
    algorithm = models.CharField(
        max_length=20, choices=ALGORITHM_CHOICES, default='rsa'
    )
    fingerprint = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)

    class Meta:
        db_table = 'ssh_keys'
        ordering = ['name']
        indexes = [
            models.Index(fields=['tenant', 'user', 'algorithm']),
        ]

    def save(self, *args, **kwargs):
        # Auto-encrypt private_key if present and not already encrypted
        if self.private_key and not self.is_encrypted:
            self.private_key = encrypt_value(self.private_key)
            self.is_encrypted = True
        # Auto-calculate fingerprint from public_key if not set
        if self.public_key and not self.fingerprint:
            try:
                parts = self.public_key.strip().split()
                key_data = base64.b64decode(parts[1])
                digest = hashlib.sha256(key_data).digest()
                self.fingerprint = 'SHA256:' + base64.b64encode(digest).decode().rstrip('=')
            except Exception:
                self.fingerprint = ''
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name
