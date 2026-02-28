"""
Stripe API wrapper — stateless service class for all Stripe operations.

Usage:
    client = StripeClient()
    customer_id = client.create_customer(tenant)
"""
import stripe
from django.conf import settings


class StripeClient:
    def __init__(self):
        stripe.api_key = settings.STRIPE_SECRET_KEY

    def create_customer(self, tenant) -> str:
        """Create a Stripe customer for a tenant. Returns stripe_customer_id."""
        owner = tenant.users.filter(is_staff=True).first() or tenant.users.first()
        customer = stripe.Customer.create(
            email=owner.email if owner else '',
            name=tenant.name,
            metadata={
                'tenant_id': str(tenant.id),
                'tenant_slug': tenant.slug,
            },
        )
        return customer.id

    def create_subscription(
        self,
        customer_id: str,
        price_id: str,
        trial_days: int = 14,
    ) -> stripe.Subscription:
        """Create a new subscription. Returns Stripe Subscription object."""
        return stripe.Subscription.create(
            customer=customer_id,
            items=[{'price': price_id}],
            trial_period_days=trial_days if trial_days > 0 else None,
            expand=['latest_invoice.payment_intent'],
        )

    def upgrade_subscription(
        self,
        stripe_sub_id: str,
        new_price_id: str,
    ) -> stripe.Subscription:
        """Upgrade/downgrade an existing subscription to a new price."""
        sub = stripe.Subscription.retrieve(stripe_sub_id)
        return stripe.Subscription.modify(
            stripe_sub_id,
            items=[{
                'id': sub['items']['data'][0]['id'],
                'price': new_price_id,
            }],
            proration_behavior='create_prorations',
        )

    def cancel_subscription(
        self,
        stripe_sub_id: str,
        at_period_end: bool = True,
    ) -> stripe.Subscription:
        """Cancel subscription. By default cancels at period end."""
        return stripe.Subscription.modify(
            stripe_sub_id,
            cancel_at_period_end=at_period_end,
        )

    def list_invoices(self, customer_id: str, limit: int = 20) -> list:
        """List invoices for a customer from Stripe."""
        return stripe.Invoice.list(customer=customer_id, limit=limit).data

    def attach_payment_method(self, customer_id: str, pm_id: str) -> None:
        """Attach a payment method to a customer."""
        stripe.PaymentMethod.attach(pm_id, customer=customer_id)

    def set_default_payment_method(self, customer_id: str, pm_id: str) -> None:
        """Set a payment method as the default for invoice payments."""
        stripe.Customer.modify(
            customer_id,
            invoice_settings={'default_payment_method': pm_id},
        )

    def construct_webhook_event(
        self,
        payload: bytes,
        sig_header: str,
    ) -> stripe.Event:
        """Verify and construct a Stripe webhook event."""
        return stripe.Webhook.construct_event(
            payload,
            sig_header,
            settings.STRIPE_WEBHOOK_SECRET,
        )

    def get_price_id(self, plan: str, billing_cycle: str) -> str:
        """Return the Stripe Price ID for a given plan and billing cycle."""
        return settings.STRIPE_PLAN_PRICES.get(plan, {}).get(billing_cycle, '')
