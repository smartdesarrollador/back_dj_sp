"""
Subscription billing models.

Subscription  — OneToOne with Tenant, tracks plan/status/Stripe IDs
Invoice       — Billing invoices (synced from Stripe), amounts in cents
PaymentMethod — Stored payment method metadata (tokenized by Stripe)
"""
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import CASCADE

from core.models import BaseModel
from apps.tenants.models import PLAN_CHOICES


LATAM_PAYMENT_TYPES = ['paypal', 'mercadopago', 'yape', 'plin', 'nequi', 'daviplata']

# Métodos de cobro manual que la plataforma ofrece al cliente: el cliente paga por su
# cuenta y sube un comprobante que un admin revisa. No confundir con
# LATAM_PAYMENT_TYPES, que describe los métodos guardados POR el cliente en
# PaymentMethod.
PAYMENT_METHOD_CHOICES = [
    ('yape',   'Yape'),
    ('paypal', 'PayPal'),
]


STATUS_CHOICES = [
    ('trialing',        'Trialing'),
    ('active',          'Active'),
    ('past_due',        'Past Due'),
    ('canceled',        'Canceled'),
    ('unpaid',          'Unpaid'),
    ('pending_payment', 'Pending Payment'),
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
    # Fin del período de gracia tras vencer un plan pagado. NULL = no está en gracia.
    # Ver prd/features/renovacion-y-expiracion-de-planes.md y ADR-008.
    grace_until = models.DateTimeField(null=True, blank=True)
    # Hitos de aviso ya enviados (ej. ["T-7", "T-3"]) — se resetea al renovar para
    # que los recordatorios no se dupliquen si el scheduler reintenta.
    renewal_reminders_sent = models.JSONField(default=list, blank=True)
    credit_balance = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
    )

    class Meta:
        db_table = 'subscriptions'
        indexes = [
            models.Index(fields=['tenant', 'status']),
            models.Index(fields=['current_period_end'], name='subs_period_end_idx'),
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
    currency = models.CharField(max_length=3, default='usd')  # describe amount_cents
    # ── Testigo histórico del cobro ───────────────────────────────────────────
    # Se HEREDAN del comprobante que originó el pago (ver PaymentProof), no se
    # recalculan al activar: la factura debe reflejar lo que vio y pagó el cliente,
    # aunque la tasa se haya movido entre el pago y la aprobación.
    #
    # NULL cuando no hubo conversión: Stripe (cobra en tarjeta, con su propia
    # moneda), activaciones por cupón 100% y todo lo anterior a estos campos.
    # NUNCA rellenar con la tasa de hoy — sería inventar un dato histórico.
    #
    # NO sumar `amount_pen_cents` en ningún agregado (MRR/ARR): no es un ingreso
    # adicional, es una anotación sobre el mismo cobro que ya está en amount_cents.
    exchange_rate = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True,
    )
    amount_pen_cents = models.PositiveIntegerField(null=True, blank=True)
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

    @property
    def amount_pen_display(self) -> str | None:
        """
        Importe en soles que el cliente transfirió de verdad, o `None` si no hubo
        conversión registrada — el llamador omite la línea, no pinta `S/ 0.00`.
        """
        if self.amount_pen_cents is None:
            return None
        return f"S/ {self.amount_pen_cents / 100:.2f}"

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


PAYMENT_PROOF_STATUS = [
    ('pending',  'Pending Review'),
    ('approved', 'Approved'),
    ('rejected', 'Rejected'),
]


class PaymentProof(BaseModel):
    """
    Captura que el cliente sube como prueba de un pago manual (Yape, PayPal).
    status='pending' hasta que un admin la revisa desde el panel o desde los enlaces
    de un clic de Telegram. `admin_token` vive en BD y no en Redis para que esos
    enlaces sigan funcionando días después.
    """
    subscription  = models.ForeignKey(
        Subscription, on_delete=CASCADE, related_name='payment_proofs'
    )
    # Método por el que se pagó. Default 'yape' para que todo lo anterior al catálogo
    # de métodos quedara clasificado sin migrar una sola fila.
    method        = models.CharField(
        max_length=20, choices=PAYMENT_METHOD_CHOICES, default='yape'
    )
    screenshot    = models.ImageField(upload_to='payment_proofs/')
    # Referencia verificable del pago cuando el método la da: el ID de transacción de
    # PayPal, que el revisor puede buscar en el panel. Yape no ofrece nada parecido, de
    # ahí que sea opcional.
    transaction_reference = models.CharField(max_length=100, blank=True, default='')
    plan          = models.CharField(max_length=20, choices=PLAN_CHOICES)
    # Ciclo pagado: determina precio y duración del período al aprobar el comprobante
    # (30 vs. 365 días). Vive aquí y no solo en Subscription porque la aprobación es
    # asíncrona — ver ADR-008, decisión 4.
    billing_cycle = models.CharField(
        max_length=10, choices=BILLING_CYCLE_CHOICES, default='monthly'
    )
    amount        = models.DecimalField(max_digits=8, decimal_places=2)  # USD — lo que se cobra
    # ── Testigo histórico del cobro real ──────────────────────────────────────
    # Cuando el método cobra en SOLES (Yape), el cliente transfiere soles y no
    # dólares. La aprobación puede tardar días, y si la tasa se mueve por medio, sin
    # estas dos columnas el importe del screenshot deja de cuadrar con lo que muestra
    # el panel y nadie puede reconstruir por qué. En los métodos que cobran en dólares
    # (PayPal) quedan a NULL: no hubo conversión que registrar.
    #
    # NO son la fuente de verdad del cobro —esa sigue siendo `amount`, en USD—:
    # son una foto de la tasa vigente al crear el comprobante. Se capturan en
    # utils.currency.capture_pen_snapshot(), único sitio que las produce.
    #
    # NULL = sin conversión registrada: comprobantes anteriores a estos campos, o
    # pagos hechos en dólares. NUNCA rellenar con la tasa de hoy: sería fabricar un
    # dato histórico.
    exchange_rate = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True,
    )
    amount_pen    = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
    )
    status        = models.CharField(
        max_length=10, choices=PAYMENT_PROOF_STATUS, default='pending'
    )
    admin_token   = models.CharField(max_length=64, unique=True, db_index=True)
    reviewed_at   = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'payment_proofs'
        indexes = [
            models.Index(fields=['status'], name='payment_proof_status_idx'),
        ]

    def __str__(self) -> str:
        return f"PaymentProof({self.subscription.tenant.slug} — {self.method} — {self.status})"


class PaymentMethodConfig(models.Model):
    """
    Datos de cobro de un método de pago manual: una fila por método.

    Sustituyó al singleton que servía cuando Yape era el único medio de pago.

    **Los campos específicos son columnas explícitas y no un JSON** a propósito: hoy
    son dos métodos con campos conocidos y el repo prefiere columnas salvo en
    catálogos abiertos (`Plan.limits`, `Plan.highlights`). Si algún día entra un
    método con una forma muy distinta —una transferencia bancaria con CCI, SWIFT y
    banco intermediario—, ese es el momento de replantearlo, no antes.

    | Campo               | Yape | PayPal |
    |---------------------|------|--------|
    | `holder_name`       |  ✔   |   ✔    |
    | `phone`             |  ✔   |        |
    | `checkout_url`      |      |   ✔    |
    | `account_email`     |      |   ✔    |

    Leer SIEMPRE vía `payment_methods.get_method_config()` / `get_enabled_methods()`.
    """
    method            = models.CharField(
        max_length=20, unique=True, choices=PAYMENT_METHOD_CHOICES
    )
    display_name      = models.CharField(max_length=50)
    # Nace apagado: un método sin configurar no debe ofrecerse al cliente. El
    # serializer además impide habilitarlo sin su dato identificador.
    is_enabled        = models.BooleanField(default=False)
    sort_order        = models.PositiveSmallIntegerField(default=0)
    instructions_note = models.TextField(blank=True, default='')
    holder_name       = models.CharField(max_length=255, blank=True, default='')
    phone             = models.CharField(max_length=30, blank=True, default='')
    checkout_url      = models.URLField(blank=True, default='')
    account_email     = models.EmailField(blank=True, default='')
    updated_at        = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'payment_method_configs'
        ordering = ['sort_order', 'method']

    def __str__(self) -> str:
        return f"{self.display_name} ({'enabled' if self.is_enabled else 'disabled'})"


class CurrencyConfig(models.Model):
    """
    Configuración de moneda de la plataforma (singleton, pk=1).

    USD es la moneda base y la única en la que se persiste dinero. Este modelo
    solo define cómo se PRESENTA: el tipo de cambio a PEN y la moneda por
    defecto del Hub. No hereda de BaseModel a propósito — es una fila única de
    configuración, igual que FooterConfig.

    Sustituyó al `exchange_rate` del antiguo singleton de Yape, que quedaba acoplado
    a un método de pago concreto siendo en realidad configuración de plataforma (el
    Hub necesita el tipo de cambio para mostrar precios en soles, pague por Yape o no).

    Leer SIEMPRE vía utils.currency.get_exchange_rate() — nunca estos campos
    directo, que salta la caché.

    Escribir SIEMPRE vía instance.save(): queryset.update() no dispara save() y
    por tanto dejaría la caché sirviendo el valor viejo hasta 5 min.
    """
    CURRENCY_CHOICES = [
        ('USD', 'US Dollar'),
        ('PEN', 'Sol peruano'),
    ]
    SOURCE_CHOICES = [
        ('manual', 'Manual'),
        ('auto',   'Automático'),
    ]

    usd_to_pen = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        default=Decimal('3.7500'),
        validators=[MinValueValidator(Decimal('0.0001'))],
    )
    # Moneda que el Hub muestra por defecto a quien no ha elegido nada. USD por
    # decisión de negocio: es la moneda en la que se cobra de verdad.
    default_display_currency = models.CharField(
        max_length=3, choices=CURRENCY_CHOICES, default='USD'
    )
    # Reservado para un fetcher automático de tasas. Hoy SIEMPRE es 'manual': la
    # vista lo fuerza y no se acepta del cliente, para que nadie pueda marcar
    # como automática una edición hecha a mano.
    source     = models.CharField(max_length=10, choices=SOURCE_CHOICES, default='manual')
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'currency_config'

    @classmethod
    def get(cls) -> 'CurrencyConfig':
        """Para ESCRITURA. Para leer, utils.currency.get_exchange_rate()."""
        obj, _ = cls.objects.get_or_create(id=1)
        return obj

    def save(self, *args, **kwargs) -> None:
        super().save(*args, **kwargs)
        from utils.currency import invalidate_currency_cache
        invalidate_currency_cache()

    def __str__(self) -> str:
        return (
            f'CurrencyConfig(USD→PEN {self.usd_to_pen}, '
            f'default {self.default_display_currency})'
        )


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
    # Overrides de utils.plans.PLAN_FEATURES (max_users, storage_gb, ...). {} = usar defaults de
    # código. Ver utils.plans.get_effective_plan_limits().
    limits        = models.JSONField(default=dict, blank=True)
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['price_monthly']

    def save(self, *args, **kwargs) -> None:
        super().save(*args, **kwargs)
        from django.core.cache import cache
        cache.delete(f'plan:limits:{self.id}')

    def __str__(self) -> str:
        return f'{self.display_name} (${self.price_monthly}/mo)'
