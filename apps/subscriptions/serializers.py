"""Serializers for subscription billing models."""
from decimal import Decimal

from django.utils import timezone
from rest_framework import serializers

from apps.services.models import TenantService
from apps.subscriptions.models import (
    CurrencyConfig,
    PaymentMethodConfig,
    Invoice,
    PaymentMethod,
    Plan,
    Subscription,
)
from utils.plans import get_effective_plan_limits
from utils.storage import get_tenant_storage_bytes


class SubscriptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Subscription
        fields = [
            'id',
            'plan',
            'status',
            'billing_cycle',
            'stripe_customer_id',
            'trial_start',
            'trial_end',
            'current_period_start',
            'current_period_end',
            'cancel_at_period_end',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields


class InvoiceSerializer(serializers.ModelSerializer):
    amount_display = serializers.SerializerMethodField()
    number = serializers.SerializerMethodField()
    amount = serializers.SerializerMethodField()
    amount_pen = serializers.SerializerMethodField()

    class Meta:
        model = Invoice
        fields = [
            'id',
            'number',
            'stripe_invoice_id',
            'amount_cents',
            'amount',
            'amount_display',
            'currency',
            # Testigo del cobro: lo que el cliente transfirió de verdad y con qué
            # tasa. `null` cuando no hubo conversión — el cliente no debe ver S/ 0.
            'exchange_rate',
            'amount_pen_cents',
            'amount_pen',
            'status',
            'pdf_url',
            'period_start',
            'period_end',
            'invoice_date',
            'due_date',
            'paid_at',
            'created_at',
        ]
        read_only_fields = fields

    def get_amount_display(self, obj) -> str:
        return obj.amount_display

    def get_number(self, obj) -> str:
        date = obj.invoice_date or obj.created_at
        return f"INV-{date.strftime('%Y%m')}-{str(obj.id)[:8].upper()}"

    def get_amount(self, obj) -> float:
        return obj.amount_cents / 100

    def get_amount_pen(self, obj) -> float | None:
        """Espeja `amount`. `None` —no 0— cuando no hubo conversión registrada."""
        return None if obj.amount_pen_cents is None else obj.amount_pen_cents / 100


class PaymentMethodSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentMethod
        fields = [
            'id',
            'type',
            'brand',
            'last4',
            'exp_month',
            'exp_year',
            'is_default',
            'external_type',
            'external_email',
            'external_phone',
            # external_account_id is intentionally excluded — write-only, sensitive
            'created_at',
        ]
        read_only_fields = fields


_LATAM_TYPES = ['paypal', 'mercadopago', 'yape', 'plin', 'nequi', 'daviplata']


class PaymentMethodCreateSerializer(serializers.Serializer):
    # Card (Stripe)
    stripe_payment_method_id = serializers.CharField(required=False, allow_blank=True)
    set_default = serializers.BooleanField(required=False, default=True)
    # LATAM external methods
    external_type = serializers.ChoiceField(choices=_LATAM_TYPES, required=False)
    external_email = serializers.EmailField(required=False, allow_blank=True)
    external_phone = serializers.CharField(required=False, allow_blank=True, max_length=20)
    external_account_id = serializers.CharField(required=False, allow_blank=True)
    is_default = serializers.BooleanField(required=False, default=True)

    def validate(self, data: dict) -> dict:
        has_stripe = bool(data.get('stripe_payment_method_id'))
        has_external = bool(data.get('external_type'))
        if not has_stripe and not has_external:
            raise serializers.ValidationError(
                'Provide stripe_payment_method_id (card) or external_type (LATAM).'
            )
        if has_stripe and has_external:
            raise serializers.ValidationError(
                'Cannot provide both stripe_payment_method_id and external_type.'
            )
        return data


class PaymentMethodUpdateSerializer(serializers.Serializer):
    is_default = serializers.BooleanField(required=False)
    external_email = serializers.EmailField(required=False, allow_blank=True)
    external_phone = serializers.CharField(required=False, allow_blank=True, max_length=20)
    external_account_id = serializers.CharField(required=False, allow_blank=True)


class PlanLimitsSerializer(serializers.Serializer):
    """Subset comercial de límites técnicos editable desde el Admin (ver Plan.limits)."""
    max_users            = serializers.IntegerField(min_value=0, required=False, allow_null=True)
    # Admite fracciones de GB (p.ej. 0.25 GB = 256 MB). Float y no Decimal: Plan.limits es un
    # JSONField y Decimal no es serializable con el encoder por defecto. Se redondea a 2 decimales.
    storage_gb           = serializers.FloatField(min_value=0, required=False, allow_null=True)
    max_projects         = serializers.IntegerField(min_value=0, required=False, allow_null=True)
    max_custom_roles     = serializers.IntegerField(min_value=0, required=False, allow_null=True)
    api_calls_per_month  = serializers.IntegerField(min_value=0, required=False, allow_null=True)
    max_image_upload_mb  = serializers.IntegerField(min_value=0, required=False, allow_null=True)
    max_file_upload_mb   = serializers.IntegerField(min_value=0, required=False, allow_null=True)

    def validate_storage_gb(self, value):
        return round(value, 2) if value is not None else value


class PlanSerializer(serializers.ModelSerializer):
    limits = serializers.SerializerMethodField()

    class Meta:
        model = Plan
        fields = [
            'id', 'display_name', 'description', 'price_monthly',
            'price_annual', 'popular', 'highlights', 'limits', 'updated_at',
        ]
        read_only_fields = ['id', 'updated_at']

    def get_limits(self, obj) -> dict:
        # Valor efectivo (override de BD + defaults de código), no el campo crudo —
        # así el form del Admin siempre se precarga con lo realmente vigente.
        return get_effective_plan_limits(obj.id)


class PlanUpdateSerializer(serializers.Serializer):
    display_name  = serializers.CharField(max_length=100, required=False)
    description   = serializers.CharField(max_length=300, required=False, allow_blank=True)
    price_monthly = serializers.IntegerField(min_value=0, required=False)
    price_annual  = serializers.IntegerField(min_value=0, required=False)
    popular       = serializers.BooleanField(required=False)
    highlights    = serializers.ListField(
        child=serializers.DictField(), required=False, min_length=1, max_length=10
    )
    limits        = PlanLimitsSerializer(required=False)

    MONTHS_PER_YEAR = 12

    def validate(self, data):
        """
        El precio anual nunca puede superar 12 mensualidades: sería cobrarle más a
        quien se compromete por más tiempo, contradiciendo el descuento que la UI
        anuncia. Es el error de captura típico —editar el mensual y olvidar el
        anual— y desde la Fase 3 el ciclo anual cobra de verdad.

        En un PATCH parcial se compara contra el valor ya guardado, que llega por
        contexto desde AdminPlanDetailView.
        """
        plan = self.context.get('plan')
        price_monthly = data.get(
            'price_monthly', getattr(plan, 'price_monthly', None)
        )
        price_annual = data.get('price_annual', getattr(plan, 'price_annual', None))

        if price_monthly is None or price_annual is None:
            return data

        max_annual = price_monthly * self.MONTHS_PER_YEAR
        if price_annual > max_annual:
            # Detalle en lista: convención del repo (ver LL-104). Aquí DRF lo
            # normalizaría igual vía as_serializer_error, pero no se depende de eso.
            raise serializers.ValidationError({
                'price_annual': [
                    f'El precio anual (${price_annual}) no puede superar 12 '
                    f'mensualidades (${max_annual}). Ajusta el precio mensual o '
                    f'baja el anual.'
                ]
            })
        return data


class UpgradeSerializer(serializers.Serializer):
    VALID_PLANS = ['free', 'starter', 'professional', 'enterprise']

    new_plan = serializers.ChoiceField(choices=VALID_PLANS)
    billing_cycle = serializers.ChoiceField(choices=['monthly', 'annual'])

    def validate(self, data):
        request = self.context.get('request')
        if request and hasattr(request, 'tenant') and request.tenant:
            current_plan = request.tenant.plan
            if data['new_plan'] == current_plan:
                raise serializers.ValidationError(
                    {'new_plan': 'New plan must be different from the current plan.'}
                )
        return data


PLAN_DISPLAY_NAMES: dict[str, str] = {
    'free': 'Free',
    'starter': 'Starter',
    'professional': 'Professional',
    'enterprise': 'Enterprise',
}


class CurrentSubscriptionSerializer(serializers.ModelSerializer):
    usage = serializers.SerializerMethodField()
    plan = serializers.SerializerMethodField()
    plan_display = serializers.SerializerMethodField()
    mrr = serializers.SerializerMethodField()
    professional_trial_used = serializers.SerializerMethodField()
    renewal_state = serializers.SerializerMethodField()
    days_until_expiry = serializers.SerializerMethodField()
    is_renewable = serializers.SerializerMethodField()
    has_pending_proof = serializers.SerializerMethodField()
    pending_plan = serializers.SerializerMethodField()

    class Meta:
        model = Subscription
        fields = [
            'id',
            'plan',
            'plan_display',
            'status',
            'billing_cycle',
            'trial_start',
            'trial_end',
            'current_period_start',
            'current_period_end',
            'grace_until',
            'cancel_at_period_end',
            'mrr',
            'created_at',
            'usage',
            'professional_trial_used',
            'renewal_state',
            'days_until_expiry',
            'is_renewable',
            'has_pending_proof',
            'pending_plan',
        ]
        read_only_fields = fields

    def get_plan(self, obj) -> str:
        # Tenant.plan es la fuente de verdad real (la que usa check_plan_limit y el topbar);
        # Subscription.plan es bookkeeping de billing y puede desincronizarse. Ver LL-049/plan
        # de fix "plan del tenant desincronizado en el Hub".
        return obj.tenant.plan

    def get_plan_display(self, obj) -> str:
        plan = obj.tenant.plan
        return PLAN_DISPLAY_NAMES.get(plan, plan.capitalize())

    def get_professional_trial_used(self, obj) -> bool:
        return obj.tenant.professional_trial_used

    def get_renewal_state(self, obj) -> str:
        # Derivado, nunca persistido. Se calcula en services.py para que el Hub y la
        # vista de pago compartan un único criterio (ver docstring de ese módulo).
        from apps.subscriptions.services import get_renewal_state
        return get_renewal_state(obj)

    def get_days_until_expiry(self, obj) -> int | None:
        """Días hasta el fin del período; negativo si ya venció, None si no hay período."""
        if obj.current_period_end is None:
            return None
        return (obj.current_period_end - timezone.now()).days

    def get_is_renewable(self, obj) -> bool:
        from apps.subscriptions.services import is_renewable
        return is_renewable(obj)

    def get_has_pending_proof(self, obj) -> bool:
        """Comprobante esperando revisión: el Hub deshabilita el CTA de pago."""
        return obj.yape_proofs.filter(status='pending').exists()

    def get_pending_plan(self, obj) -> str | None:
        """
        Plan que el tenant eligió al registrarse y nunca llegó a pagar, o None.

        El registro con plan pagado deja `Subscription.plan` con el elegido y el tenant en
        `free` (`plan` arriba devuelve `tenant.plan` a propósito, LL-049). Quien abandona el
        paso de pago queda así: con una cuenta usable en Free y sin ninguna señal de qué
        contrató. Exigir `tenant.plan == 'free'` evita anunciar "pendiente" a quien ya tiene
        el plan activo y solo arrastra un `status` desactualizado.
        """
        if obj.status != 'pending_payment' or obj.tenant.plan != 'free':
            return None
        return obj.plan

    def get_mrr(self, obj) -> float:
        last_paid = (
            obj.tenant.invoices.filter(status='paid')
            .order_by('-created_at')
            .values_list('amount_cents', flat=True)
            .first()
        )
        return round((last_paid or 0) / 100, 2)

    def get_usage(self, obj) -> dict:
        tenant = obj.tenant
        plan_config = get_effective_plan_limits(tenant.plan)

        user_count = tenant.users.count()
        service_count = TenantService.objects.filter(tenant=tenant, status='active').count()

        return {
            'users': {
                'current': user_count,
                'limit': plan_config.get('max_users'),
            },
            'storage': {
                'current_gb': round(get_tenant_storage_bytes(tenant) / 1024 ** 3, 3),
                'limit_gb': plan_config.get('storage_gb'),
            },
            'services': {
                'current': service_count,
                'limit': None,
            },
        }


class CurrencyConfigSerializer(serializers.ModelSerializer):
    """Lectura admin. `rates` replica la forma del endpoint público."""

    rates = serializers.SerializerMethodField()
    updated_by_email = serializers.CharField(
        source='updated_by.email', default=None, read_only=True
    )

    class Meta:
        model = CurrencyConfig
        fields = [
            'usd_to_pen', 'default_display_currency', 'source',
            'rates', 'updated_at', 'updated_by_email',
        ]
        read_only_fields = fields

    def get_rates(self, obj) -> dict:
        return {'USD': '1.0000', 'PEN': str(obj.usd_to_pen)}


class CurrencyConfigUpdateSerializer(serializers.Serializer):
    """
    Escritura admin del tipo de cambio.

    `source` NO es escribible a propósito: la vista lo fuerza a 'manual'. Si
    viniera del cliente, un PATCH podría marcar como 'auto' una edición hecha a
    mano y confundir a cualquier futuro fetcher automático.
    """

    usd_to_pen = serializers.DecimalField(
        max_digits=10, decimal_places=4, required=False
    )
    default_display_currency = serializers.ChoiceField(
        choices=[c[0] for c in CurrencyConfig.CURRENCY_CHOICES], required=False
    )

    # Guardarraíl contra el error de captura por orden de magnitud (375 en vez de
    # 3.75, o 0.375). El rango es deliberadamente amplio y no una banda alrededor
    # del valor actual: protege del dedazo sin bloquear una devaluación real.
    MIN_RATE = Decimal('1.0000')
    MAX_RATE = Decimal('20.0000')

    def validate_usd_to_pen(self, value: Decimal) -> Decimal:
        if not (self.MIN_RATE <= value <= self.MAX_RATE):
            # Detalle en LISTA: convención del repo (LL-104). Con un string suelto,
            # core/exceptions.py lo degradaría a un genérico "Validation error" y el
            # admin no vería el motivo.
            raise serializers.ValidationError([
                f'El tipo de cambio ({value}) está fuera del rango permitido '
                f'({self.MIN_RATE} – {self.MAX_RATE}). Verifica que sean soles '
                f'por dólar (ej. 3.7500), no céntimos.'
            ])
        return value

    def validate(self, data):
        if not data:
            raise serializers.ValidationError({
                'non_field_errors': ['Debes enviar al menos un campo a actualizar.']
            })
        return data


class PaymentMethodConfigSerializer(serializers.ModelSerializer):
    """Lectura admin: incluye los métodos apagados y si están configurados."""

    is_configured = serializers.SerializerMethodField()

    class Meta:
        model = PaymentMethodConfig
        fields = [
            'method', 'display_name', 'is_enabled', 'is_configured', 'sort_order',
            'holder_name', 'phone', 'checkout_url', 'account_email',
            'instructions_note', 'updated_at',
        ]
        read_only_fields = ['method', 'is_configured', 'updated_at']

    def get_is_configured(self, obj) -> bool:
        from apps.subscriptions.payment_methods import is_configured
        return is_configured(obj)


class PaymentMethodConfigUpdateSerializer(serializers.Serializer):
    """
    Escritura admin. `method` no es editable: identifica la fila.

    El guardarraíl importante es `validate`: **no se puede habilitar un método sin su
    dato identificador**. Publicar un método al que el cliente no puede pagar lo lleva
    hasta el final del flujo para dejarlo sin destino de pago, y es el error más fácil
    de cometer aquí.
    """

    display_name      = serializers.CharField(max_length=50, required=False)
    is_enabled        = serializers.BooleanField(required=False)
    sort_order        = serializers.IntegerField(min_value=0, required=False)
    holder_name       = serializers.CharField(max_length=255, required=False, allow_blank=True)
    phone             = serializers.CharField(max_length=30, required=False, allow_blank=True)
    checkout_url      = serializers.URLField(required=False, allow_blank=True)
    account_email     = serializers.EmailField(required=False, allow_blank=True)
    instructions_note = serializers.CharField(required=False, allow_blank=True)

    def validate(self, data):
        if not data:
            raise serializers.ValidationError({
                'non_field_errors': ['Debes enviar al menos un campo a actualizar.']
            })

        from apps.subscriptions.payment_methods import REQUIRED_FIELDS

        config = self.context.get('config')
        if config is None:
            return data

        # Se evalúa sobre el estado RESULTANTE, no sobre el payload: habilitar en un
        # PATCH y borrar el teléfono en otro dejaría el método publicado y sin destino.
        will_be_enabled = data.get('is_enabled', config.is_enabled)
        if not will_be_enabled:
            return data

        required = REQUIRED_FIELDS.get(config.method, ())
        if required and not any(
            data.get(field, getattr(config, field, '')) for field in required
        ):
            labels = {
                'phone':         'un número de teléfono',
                'checkout_url':  'un enlace de pago',
                'account_email': 'un correo de la cuenta',
            }
            needed = ' o '.join(labels.get(f, f) for f in required)
            # Detalle en LISTA (LL-104).
            raise serializers.ValidationError({
                'is_enabled': [
                    f'No se puede habilitar {config.display_name} sin {needed}: '
                    f'el cliente llegaría al paso de pago sin saber a dónde pagar.'
                ]
            })
        return data
