"""
Promociones (códigos de descuento / cupones).

El cupón ES el campo `code` de la promoción (modelo único, relación 1:1).
Contrato de campos alineado a la UI del Admin Panel
(apps/frontend_admin/src/features/promotions/types.ts).
Ver prd/features/promo-codes-registro.md.
"""
from django.db import models
from django.utils import timezone

from core.models import BaseModel

PROMOTION_TYPES = [
    ('percentage', 'Porcentaje'),
    ('fixed_amount', 'Monto fijo'),
    # 'trial_extension' reservado para una fase posterior (existe en la UI, deshabilitado en v1)
]

REDEMPTION_STATUS = [
    ('pending', 'Pending'),      # comprobante Yape subido, pago sin revisar
    ('confirmed', 'Confirmed'),  # pago aprobado — el uso cuenta contra max_uses
    ('released', 'Released'),    # pago rechazado — el uso se libera
]

APPLICABLE_PLANS = ['starter', 'professional', 'enterprise']


class Promotion(BaseModel):
    """
    Código de descuento canjeable en el registro (pago Yape manual).
    `status` es computado (propiedad), no columna: las validaciones de canje
    evalúan las condiciones en vivo, sin tareas periódicas.
    """
    code                  = models.CharField(max_length=20, unique=True, db_index=True)
    # ^[A-Z0-9]{3,20}$ — normalizado a uppercase en el serializer; inmutable tras creación
    name                  = models.CharField(max_length=100)
    description           = models.TextField(blank=True, default='')

    type                  = models.CharField(max_length=20, choices=PROMOTION_TYPES)
    value                 = models.DecimalField(max_digits=10, decimal_places=2)
    max_discount          = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )  # cap en USD, solo para type=percentage; NULL = sin tope

    applicable_plans      = models.JSONField(default=list)  # ["starter", "professional"]
    new_customers_only    = models.BooleanField(default=True)

    starts_at             = models.DateTimeField()
    expires_at            = models.DateTimeField()
    max_uses              = models.IntegerField(null=True, blank=True)  # NULL = ilimitado
    max_uses_per_customer = models.IntegerField(default=1)
    current_uses          = models.IntegerField(default=0)  # solo redemptions confirmadas
    last_used_at          = models.DateTimeField(null=True, blank=True)

    is_paused             = models.BooleanField(default=False)

    class Meta:
        db_table = 'promotions'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['is_paused', 'expires_at'], name='promo_paused_expires_idx'),
        ]

    @property
    def status(self) -> str:
        if self.is_paused:
            return 'paused'
        if timezone.now() > self.expires_at:
            return 'expired'
        if self.max_uses is not None and self.current_uses >= self.max_uses:
            return 'depleted'
        return 'active'

    def __str__(self) -> str:
        return f'Promotion({self.code} — {self.status})'


class PromotionRedemption(BaseModel):
    """
    Canje de una promoción por un tenant, atado al comprobante de pago Yape.
    pending → confirmed (aprobación del pago, incrementa current_uses con lock)
    pending → released  (rechazo del pago, el uso no se consume)
    yape_proof es NULL en el caso de descuento 100% (activación directa, sin comprobante).
    """
    promotion       = models.ForeignKey(
        Promotion, on_delete=models.PROTECT, related_name='redemptions'
    )
    tenant          = models.ForeignKey(
        'tenants.Tenant', on_delete=models.CASCADE, related_name='promo_redemptions'
    )
    yape_proof      = models.OneToOneField(
        'subscriptions.YapePaymentProof', on_delete=models.CASCADE,
        null=True, blank=True, related_name='redemption',
    )
    plan            = models.CharField(max_length=20)
    original_amount = models.DecimalField(max_digits=8, decimal_places=2)  # USD
    discount_amount = models.DecimalField(max_digits=8, decimal_places=2)  # USD
    final_amount    = models.DecimalField(max_digits=8, decimal_places=2)  # USD

    status          = models.CharField(max_length=10, choices=REDEMPTION_STATUS, default='pending')
    confirmed_at    = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'promotion_redemptions'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['promotion', 'status'], name='promo_redemption_status_idx'),
            models.Index(fields=['tenant', 'promotion'], name='promo_redemption_tenant_idx'),
        ]

    def __str__(self) -> str:
        return f'Redemption({self.promotion.code} — {self.tenant.slug} — {self.status})'
