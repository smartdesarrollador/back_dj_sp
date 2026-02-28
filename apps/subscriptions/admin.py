from django.contrib import admin

from apps.subscriptions.models import Invoice, PaymentMethod, Subscription


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = [
        'tenant', 'plan', 'status', 'billing_cycle', 'cancel_at_period_end', 'created_at'
    ]
    list_filter = ['plan', 'status', 'billing_cycle']
    search_fields = ['tenant__name', 'tenant__slug']
    readonly_fields = [
        'id', 'stripe_subscription_id', 'stripe_customer_id', 'created_at', 'updated_at'
    ]


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'amount_display', 'status', 'invoice_date', 'created_at']
    list_filter = ['status', 'currency']
    search_fields = ['tenant__name', 'tenant__slug', 'stripe_invoice_id']
    readonly_fields = [
        'id', 'stripe_invoice_id', 'amount_display', 'created_at', 'updated_at'
    ]


@admin.register(PaymentMethod)
class PaymentMethodAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'brand', 'last4', 'type', 'is_default', 'created_at']
    list_filter = ['type', 'brand', 'is_default']
    search_fields = ['tenant__name', 'tenant__slug', 'last4']
    readonly_fields = ['id', 'stripe_payment_method_id', 'created_at', 'updated_at']
