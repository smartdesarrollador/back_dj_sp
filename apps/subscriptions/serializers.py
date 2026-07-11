"""Serializers for subscription billing models."""
from rest_framework import serializers

from apps.services.models import TenantService
from apps.subscriptions.models import Invoice, PaymentMethod, Plan, Subscription
from utils.plans import PLAN_FEATURES
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

    class Meta:
        model = Invoice
        fields = [
            'id',
            'stripe_invoice_id',
            'amount_cents',
            'amount_display',
            'currency',
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


class PlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = Plan
        fields = [
            'id', 'display_name', 'description', 'price_monthly',
            'price_annual', 'popular', 'highlights', 'updated_at',
        ]
        read_only_fields = ['id', 'updated_at']


class PlanUpdateSerializer(serializers.Serializer):
    display_name  = serializers.CharField(max_length=100, required=False)
    description   = serializers.CharField(max_length=300, required=False, allow_blank=True)
    price_monthly = serializers.IntegerField(min_value=0, required=False)
    price_annual  = serializers.IntegerField(min_value=0, required=False)
    popular       = serializers.BooleanField(required=False)
    highlights    = serializers.ListField(
        child=serializers.DictField(), required=False, min_length=1, max_length=10
    )


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
    plan_display = serializers.SerializerMethodField()
    mrr = serializers.SerializerMethodField()
    professional_trial_used = serializers.SerializerMethodField()

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
            'cancel_at_period_end',
            'mrr',
            'created_at',
            'usage',
            'professional_trial_used',
        ]
        read_only_fields = fields

    def get_plan_display(self, obj) -> str:
        return PLAN_DISPLAY_NAMES.get(obj.plan, obj.plan.capitalize())

    def get_professional_trial_used(self, obj) -> bool:
        return obj.tenant.professional_trial_used

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
        plan = obj.plan
        plan_config = PLAN_FEATURES.get(plan, PLAN_FEATURES['free'])

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
