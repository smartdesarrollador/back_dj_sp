"""
Subscription billing models.

Subscription  — OneToOne with Tenant, tracks plan/status/Stripe IDs
Invoice       — Billing invoices (synced from Stripe), amounts in cents
PaymentMethod — Stored payment method metadata (tokenized by Stripe)
"""
from decimal import Decimal

from django.db import models
from django.db.models import CASCADE

from core.models import BaseModel
from apps.tenants.models import PLAN_CHOICES


LATAM_PAYMENT_TYPES = ['paypal', 'mercadopago', 'yape', 'plin', 'nequi', 'daviplata']


STATUS_CHOICES = [
    ('trialing', 'Trialing'),
    ('active', 'Active'),
    ('past_due', 'Past Due'),
    ('canceled', 'Canceled'),
    ('unpaid', 'Unpaid'),
]

BILLING_CYCLE_CHOICES = [
    ('monthly', 'Monthly'),
    ('annual', 'Annual'),
]

INVOICE_STATUS = [
    ('draft', 'Draft'),
    ('open', 'Open'),
    ('paid', 'Paid'),
    ('void', 'Void'),
    ('uncollectible', 'Uncollectible'),
]


class Subscription(BaseModel):
    """
    Tracks a tenant's subscription plan and billing status.
    OneToOne with Tenant — each tenant has exactly one subscription.
    """
    tenant = models.OneToOneField(
        'tenants.Tenant',
        on_delete=CASCADE,
        related_name='subscription',
    )
    plan = models.CharField(max_length=20, choices=PLAN_CHOICES, default='free')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='trialing')
    billing_cycle = models.CharField(
        max_length=10, choices=BILLING_CYCLE_CHOICES, default='monthly'
    )
    # Stripe IDs
    stripe_subscription_id = models.CharField(max_length=255, blank=True)
    stripe_customer_id = models.CharField(max_length=255, blank=True)
    # Trial period
    trial_start = models.DateTimeField(null=True, blank=True)
    trial_end = models.DateTimeField(null=True, blank=True)
    # Billing period
    current_period_start = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    cancel_at_period_end = models.BooleanField(default=False)
    credit_balance = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
    )

    class Meta:
        db_table = 'subscriptions'
        indexes = [
            models.Index(fields=['tenant', 'status']),
        ]

    def __str__(self) -> str:
        return f"{self.tenant.slug} — {self.plan} ({self.status})"


class Invoice(BaseModel):
    """
    Invoice record synced from Stripe.
    Amounts stored in cents to match Stripe's integer representation.
    """
    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=CASCADE,
        related_name='invoices',
    )
    stripe_invoice_id = models.CharField(max_length=255, unique=True, blank=True)
    amount_cents = models.PositiveIntegerField(default=0)  # cents USD
    currency = models.CharField(max_length=3, default='usd')
    status = models.CharField(max_length=20, choices=INVOICE_STATUS, default='draft')
    pdf_url = models.URLField(blank=True)
    period_start = models.DateTimeField(null=True, blank=True)
    period_end = models.DateTimeField(null=True, blank=True)
    invoice_date = models.DateTimeField(null=True, blank=True)
    due_date = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'invoices'
        indexes = [
            models.Index(fields=['tenant', 'status']),
        ]

    @property
    def amount_display(self) -> str:
        """Convert cents to formatted dollar amount."""
        return f"${self.amount_cents / 100:.2f}"

    def __str__(self) -> str:
        return f"{self.tenant.slug} — {self.amount_display} ({self.status})"


class PaymentMethod(BaseModel):
    """
    Stored payment method — either a Stripe card or a LATAM external method.
    Stripe methods: stripe_payment_method_id populated, type='card'.
    LATAM methods: external_type set, type='external', account_id AES-256 encrypted.
    """
    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=CASCADE,
        related_name='payment_methods',
    )
    stripe_payment_method_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    type = models.CharField(max_length=20, default='card')  # 'card', 'external'
    brand = models.CharField(max_length=20, blank=True)     # 'visa', 'mastercard'
    last4 = models.CharField(max_length=4, blank=True)
    exp_month = models.PositiveSmallIntegerField(null=True, blank=True)
    exp_year = models.PositiveSmallIntegerField(null=True, blank=True)
    is_default = models.BooleanField(default=False)
    # LATAM / external payment methods
    external_type = models.CharField(max_length=20, blank=True)
    # 'paypal' | 'mercadopago' | 'yape' | 'plin' | 'nequi' | 'daviplata'
    external_email = models.EmailField(blank=True)          # PayPal, MercadoPago
    external_phone = models.CharField(max_length=20, blank=True)  # Yape, Plin, Nequi, Daviplata
    external_account_id = models.TextField(blank=True)      # AES-256 encrypted

    class Meta:
        db_table = 'payment_methods'
        indexes = [
            models.Index(fields=['tenant']),
        ]

    def save(self, *args, **kwargs):
        # Ensure only one default per tenant
        if self.is_default:
            PaymentMethod.objects.filter(
                tenant=self.tenant, is_default=True
            ).exclude(pk=self.pk).update(is_default=False)
        # Encrypt external_account_id if present and not already encrypted
        if self.external_account_id and not self.external_account_id.startswith('gAAAAA'):
            from utils.encryption import encrypt_value
            self.external_account_id = encrypt_value(self.external_account_id)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        if self.external_type:
            return f"{self.tenant.slug} — {self.external_type}"
        return f"{self.tenant.slug} — {self.brand} ****{self.last4}"


YAPE_PROOF_STATUS = [
    ('pending',  'Pending Review'),
    ('approved', 'Approved'),
    ('rejected', 'Rejected'),
]


class YapePaymentProof(BaseModel):
    """
    Screenshot uploaded by a user as proof of manual Yape payment.
    status='pending' until an admin reviews via the one-click Telegram links.
    admin_token is stored in DB (not Redis) so approve/reject links work days later.
    """
    subscription  = models.ForeignKey(
        Subscription, on_delete=CASCADE, related_name='yape_proofs'
    )
    screenshot    = models.ImageField(upload_to='yape_proofs/')
    plan          = models.CharField(max_length=20, choices=PLAN_CHOICES)
    amount        = models.DecimalField(max_digits=8, decimal_places=2)
    status        = models.CharField(max_length=10, choices=YAPE_PROOF_STATUS, default='pending')
    admin_token   = models.CharField(max_length=64, unique=True, db_index=True)
    reviewed_at   = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'yape_payment_proofs'
        indexes = [
            models.Index(fields=['status'], name='yape_proof_status_idx'),
        ]

    def __str__(self) -> str:
        return f"YapeProof({self.subscription.tenant.slug} — {self.plan} — {self.status})"


class YapeConfig(models.Model):
    """
    Singleton configuration for the manual Yape payment method.
    Always access via YapeConfig.get() — creates the record on first use.
    """
    phone             = models.CharField(max_length=30, default='')
    holder_name       = models.CharField(max_length=255, default='')
    is_enabled        = models.BooleanField(default=True)
    exchange_rate     = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('3.75'))
    instructions_note = models.TextField(blank=True, default='')
    updated_at        = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'yape_config'

    @classmethod
    def get(cls) -> 'YapeConfig':
        obj, _ = cls.objects.get_or_create(id=1)
        return obj

    def __str__(self) -> str:
        return f"YapeConfig({self.phone} — {'enabled' if self.is_enabled else 'disabled'})"


class Plan(models.Model):
    """
    Presentation metadata for subscription plans.
    IDs are immutable (free/starter/professional/enterprise).
    Prices and highlights are editable by admins without code changes.
    """
    id            = models.CharField(max_length=20, primary_key=True, choices=PLAN_CHOICES)
    display_name  = models.CharField(max_length=100)
    description   = models.CharField(max_length=300, blank=True)
    price_monthly = models.IntegerField(default=0)
    price_annual  = models.IntegerField(default=0)
    popular       = models.BooleanField(default=False)
    highlights    = models.JSONField(default=list)   # [{ "label": str, "included": bool }]
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['price_monthly']

    def __str__(self) -> str:
        return f'{self.display_name} (${self.price_monthly}/mo)'
