"""
Vault models — personal encrypted secrets protected by a user master password.

Authorization is per-user (a vault is personal, not shared inside the tenant).
Secret payloads live in `VaultItem.data_ciphertext`, encrypted with a per-user DEK
that is itself wrapped by a key derived from the master password (see crypto.py).
`title` and `item_type` stay in clear so the list is browsable while locked.
"""
from django.conf import settings
from django.db import models

from core.models import BaseModel


class VaultKey(BaseModel):
    """Per-user key material for the vault (one row per user)."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='vault_key',
    )
    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='vault_keys',
    )
    # Master password → KEK
    salt = models.CharField(max_length=64)
    wrapped_dek = models.TextField()
    master_verifier = models.CharField(max_length=255)
    # Recovery code → KEK (wraps the same DEK)
    recovery_salt = models.CharField(max_length=64)
    wrapped_dek_recovery = models.TextField()
    recovery_verifier = models.CharField(max_length=255)
    recovery_used_at = models.DateTimeField(null=True, blank=True)
    # Sharing: X25519 keypair. Public key is not sensitive (stored in clear);
    # the private key is wrapped twice — once per KEK, mirroring wrapped_dek /
    # wrapped_dek_recovery above — so both the master password and the
    # recovery code can unwrap it independently. Blank until the user unlocks
    # their vault at least once after this field was introduced (see
    # VaultUnlockView — lazy backfill for pre-existing VaultKey rows).
    public_key = models.CharField(max_length=64, blank=True)
    wrapped_private_key = models.TextField(blank=True)
    wrapped_private_key_recovery = models.TextField(blank=True)

    class Meta:
        db_table = 'vault_keys'

    def __str__(self) -> str:
        return f'VaultKey<{self.user_id}>'


class VaultItem(BaseModel):
    ITEM_TYPES = [
        ('login', 'Login'),
        ('api_key', 'API Key'),
        ('secure_note', 'Secure Note'),
        ('card', 'Card'),
    ]

    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='vault_items',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='vault_items',
    )
    title = models.CharField(max_length=255)
    item_type = models.CharField(max_length=20, choices=ITEM_TYPES, default='login')
    data_ciphertext = models.TextField()
    favorite = models.BooleanField(default=False)

    class Meta:
        db_table = 'vault_items'
        ordering = ['-favorite', 'title']
        indexes = [
            models.Index(fields=['tenant', 'user', 'item_type']),
        ]

    def __str__(self) -> str:
        return f'{self.title} [{self.item_type}]'


class VaultShare(BaseModel):
    """
    Grants a specific user read access to a VaultItem, without ever exposing it
    to the server outside of a request context (see crypto.py — sealed-box).
    `sealed_payload` holds the item's plaintext re-encrypted for `shared_with`'s
    public key; only `shared_with`'s own private key (behind their own master
    password) can open it. Re-sealed on every update to the source item.
    """
    item = models.ForeignKey(
        VaultItem,
        on_delete=models.CASCADE,
        related_name='shares',
    )
    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='vault_shares',
    )
    shared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='vault_shares_given',
    )
    shared_with = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='vault_shares_received',
    )
    sealed_payload = models.TextField()

    class Meta:
        db_table = 'vault_shares'
        unique_together = [('item', 'shared_with')]
        indexes = [
            models.Index(fields=['tenant', 'shared_with']),
        ]

    def __str__(self) -> str:
        return f'{self.shared_with} → {self.item_id}'
