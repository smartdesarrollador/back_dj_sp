"""
Referral system models.

ReferralCode — unique shareable code per tenant
Referral     — tracks a referred relationship between two tenants
"""
import uuid
from decimal import Decimal

from django.db import models
from django.utils.text import slugify

from core.models import BaseModel


class ReferralCode(BaseModel):
    tenant = models.OneToOneField(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='referral_code',
    )
    code = models.CharField(max_length=50, unique=True, db_index=True)

    class Meta:
        db_table = 'referral_codes'

    def __str__(self) -> str:
        return self.code

    @classmethod
    def generate_code(cls, tenant) -> str:
        name_part = slugify(tenant.name).upper().replace('-', '')[:8]
        uid_part = str(uuid.uuid4())[:4].upper()
        return f'REF-{name_part}-{uid_part}'


REFERRAL_STATUS_CHOICES = [
    ('pending', 'Pendiente'),
    ('active', 'Activo'),
    ('expired', 'Expirado'),
]


class Referral(BaseModel):
    referrer = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='given_referrals',
    )
    referred = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='received_referrals',
    )
    status = models.CharField(
        max_length=20,
        choices=REFERRAL_STATUS_CHOICES,
        default='pending',
        db_index=True,
    )
    credit_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('29.00'),
    )
    activated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'referrals'
        unique_together = [['referrer', 'referred']]
        indexes = [
            models.Index(fields=['referrer', 'status']),
        ]

    def __str__(self) -> str:
        return f'{self.referrer} → {self.referred} ({self.status})'
