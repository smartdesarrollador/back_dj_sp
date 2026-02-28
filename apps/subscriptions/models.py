"""
Subscription billing models.

Subscription  — OneToOne with Tenant, tracks plan/status/Stripe IDs
Invoice       — Billing invoices (synced from Stripe), amounts in cents
PaymentMethod — Stored payment method metadata (tokenized by Stripe)
"""
from django.db import models
from django.db.models import CASCADE

from core.models import BaseModel
from apps.tenants.models import PLAN_CHOICES


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
    Tokenized payment method stored in Stripe.
    Only metadata is stored locally — no raw card numbers.
    """
    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=CASCADE,
        related_name='payment_methods',
    )
    stripe_payment_method_id = models.CharField(max_length=255, unique=True)
    type = models.CharField(max_length=20, default='card')  # 'card', 'bank_account'
    brand = models.CharField(max_length=20, blank=True)     # 'visa', 'mastercard'
    last4 = models.CharField(max_length=4, blank=True)
    exp_month = models.PositiveSmallIntegerField(null=True, blank=True)
    exp_year = models.PositiveSmallIntegerField(null=True, blank=True)
    is_default = models.BooleanField(default=False)

    class Meta:
        db_table = 'payment_methods'
        indexes = [
            models.Index(fields=['tenant']),
        ]

    def save(self, *args, **kwargs):
        if self.is_default:
            # Ensure only one default per tenant
            PaymentMethod.objects.filter(
                tenant=self.tenant, is_default=True
            ).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.tenant.slug} — {self.brand} ****{self.last4}"
