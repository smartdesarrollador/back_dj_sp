"""
Subscription billing views.

CurrentSubscriptionView  — GET  /api/v1/admin/subscriptions/current
UpgradeSubscriptionView  — POST /api/v1/admin/subscriptions/upgrade
CancelSubscriptionView   — POST /api/v1/admin/subscriptions/cancel
InvoiceListView          — GET  /api/v1/admin/billing/invoices
WebhookView              — POST /api/v1/admin/billing/webhooks
PaymentMethodView        — GET  /api/v1/admin/billing/payment-methods
PaymentMethodCreateView  — POST /api/v1/admin/billing/payment-methods/create
"""
import logging
from datetime import datetime, timedelta, timezone as dt_tz

import stripe
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.rbac.permissions import HasPermission
from apps.subscriptions.models import Invoice, PaymentMethod, Subscription
from apps.subscriptions.serializers import (
    CurrentSubscriptionSerializer,
    InvoiceSerializer,
    PaymentMethodSerializer,
    SubscriptionSerializer,
    UpgradeSerializer,
)
from apps.subscriptions.stripe_client import StripeClient

logger = logging.getLogger(__name__)


def _get_tenant(request):
    """Get tenant from request.tenant (middleware) or request.user.tenant."""
    if hasattr(request, 'tenant') and request.tenant:
        return request.tenant
    return getattr(request.user, 'tenant', None)


def _ts_to_dt(timestamp):
    """Convert Unix timestamp to UTC datetime, or None."""
    if not timestamp:
        return None
    return datetime.fromtimestamp(timestamp, tz=dt_tz.utc)


# ─── Subscription Views ────────────────────────────────────────────────────────

class CurrentSubscriptionView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tenant = _get_tenant(request)
        if not tenant:
            return Response(
                {'error': {'code': 'tenant_not_found', 'message': 'Tenant not found.'}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        subscription, _ = Subscription.objects.get_or_create(
            tenant=tenant,
            defaults={
                'plan': 'free',
                'status': 'trialing',
                'trial_start': timezone.now(),
                'trial_end': timezone.now() + timedelta(days=14),
            },
        )
        serializer = CurrentSubscriptionSerializer(subscription)
        return Response({'subscription': serializer.data})


class UpgradeSubscriptionView(APIView):
    permission_classes = [IsAuthenticated, HasPermission('subscriptions.manage')]

    def post(self, request):
        tenant = _get_tenant(request)
        if not tenant:
            return Response(
                {'error': {'code': 'tenant_not_found', 'message': 'Tenant not found.'}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = UpgradeSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return Response(
                {'error': {'code': 'validation_error', 'message': str(serializer.errors)}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        new_plan = serializer.validated_data['new_plan']
        billing_cycle = serializer.validated_data['billing_cycle']

        subscription, _ = Subscription.objects.get_or_create(
            tenant=tenant,
            defaults={'plan': 'free', 'status': 'trialing'},
        )

        stripe_client = StripeClient()
        price_id = stripe_client.get_price_id(new_plan, billing_cycle)

        try:
            if subscription.stripe_subscription_id:
                stripe_sub = stripe_client.upgrade_subscription(
                    subscription.stripe_subscription_id, price_id
                )
            else:
                # First activation — ensure customer exists
                if not subscription.stripe_customer_id:
                    customer_id = stripe_client.create_customer(tenant)
                    subscription.stripe_customer_id = customer_id

                stripe_sub = stripe_client.create_subscription(
                    subscription.stripe_customer_id, price_id, trial_days=0
                )

            with transaction.atomic():
                subscription.plan = new_plan
                subscription.billing_cycle = billing_cycle
                subscription.stripe_subscription_id = stripe_sub['id']
                subscription.status = stripe_sub.get('status', 'active')
                subscription.save()

                tenant.plan = new_plan
                tenant.save(update_fields=['plan', 'updated_at'])

        except stripe.error.StripeError as e:
            logger.error('Stripe error during upgrade: %s', str(e))
            return Response(
                {'error': {'code': 'stripe_error', 'message': str(e)}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({'subscription': SubscriptionSerializer(subscription).data})


class CancelSubscriptionView(APIView):
    permission_classes = [IsAuthenticated, HasPermission('subscriptions.cancel')]

    def post(self, request):
        tenant = _get_tenant(request)
        if not tenant:
            return Response(
                {'error': {'code': 'tenant_not_found', 'message': 'Tenant not found.'}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            subscription = Subscription.objects.get(tenant=tenant)
        except Subscription.DoesNotExist:
            return Response(
                {'error': {'code': 'not_found', 'message': 'No active subscription found.'}},
                status=status.HTTP_404_NOT_FOUND,
            )

        if subscription.stripe_subscription_id:
            try:
                stripe_client = StripeClient()
                stripe_client.cancel_subscription(
                    subscription.stripe_subscription_id, at_period_end=True
                )
            except stripe.error.StripeError as e:
                logger.error('Stripe error during cancel: %s', str(e))
                return Response(
                    {'error': {'code': 'stripe_error', 'message': str(e)}},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        subscription.cancel_at_period_end = True
        subscription.save(update_fields=['cancel_at_period_end', 'updated_at'])

        return Response({'subscription': SubscriptionSerializer(subscription).data})


# ─── Billing Views ─────────────────────────────────────────────────────────────

class InvoiceListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tenant = _get_tenant(request)
        if not tenant:
            return Response(
                {'error': {'code': 'tenant_not_found', 'message': 'Tenant not found.'}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        refresh = request.query_params.get('refresh', 'false').lower() == 'true'
        if refresh:
            self._sync_invoices_from_stripe(tenant)

        invoices = Invoice.objects.filter(tenant=tenant).order_by('-invoice_date')
        serializer = InvoiceSerializer(invoices, many=True)
        return Response({'invoices': serializer.data})

    def _sync_invoices_from_stripe(self, tenant) -> None:
        """Sync invoices from Stripe API into local DB."""
        try:
            subscription = tenant.subscription
            if not subscription.stripe_customer_id:
                return
        except Subscription.DoesNotExist:
            return

        stripe_client = StripeClient()
        stripe_invoices = stripe_client.list_invoices(subscription.stripe_customer_id)

        for si in stripe_invoices:
            Invoice.objects.update_or_create(
                stripe_invoice_id=si['id'],
                defaults={
                    'tenant': tenant,
                    'amount_cents': si.get('amount_paid') or si.get('amount_due', 0),
                    'currency': si.get('currency', 'usd'),
                    'status': si.get('status', 'draft'),
                    'pdf_url': si.get('invoice_pdf', ''),
                    'period_start': _ts_to_dt(si.get('period_start')),
                    'period_end': _ts_to_dt(si.get('period_end')),
                    'invoice_date': _ts_to_dt(si.get('created')),
                    'due_date': _ts_to_dt(si.get('due_date')),
                    'paid_at': _ts_to_dt(
                        si.get('status_transitions', {}).get('paid_at')
                    ),
                },
            )


class WebhookView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        sig_header = request.META.get('HTTP_STRIPE_SIGNATURE', '')
        payload = request.body

        stripe_client = StripeClient()
        try:
            event = stripe_client.construct_webhook_event(payload, sig_header)
        except (stripe.error.SignatureVerificationError, ValueError) as e:
            logger.warning('Invalid Stripe webhook signature: %s', str(e))
            return Response(
                {'error': 'Invalid signature'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        event_type = event['type']
        data = event['data']['object']

        try:
            if event_type == 'invoice.payment_succeeded':
                self._handle_invoice_paid(data)
            elif event_type == 'invoice.payment_failed':
                self._handle_invoice_failed(data)
            elif event_type == 'customer.subscription.updated':
                self._handle_subscription_updated(data)
            elif event_type == 'customer.subscription.deleted':
                self._handle_subscription_deleted(data)
            else:
                logger.info('Unhandled Stripe event type: %s', event_type)
        except Exception as e:
            logger.error('Error processing webhook %s: %s', event_type, str(e))

        # Always return 200 — Stripe requires this to prevent retries
        return Response({'received': True}, status=status.HTTP_200_OK)

    def _handle_invoice_paid(self, data) -> None:
        stripe_invoice_id = data.get('id', '')
        stripe_sub_id = data.get('subscription', '')

        Invoice.objects.filter(stripe_invoice_id=stripe_invoice_id).update(
            status='paid',
            paid_at=timezone.now(),
        )
        if stripe_sub_id:
            Subscription.objects.filter(
                stripe_subscription_id=stripe_sub_id
            ).update(status='active')

    def _handle_invoice_failed(self, data) -> None:
        stripe_invoice_id = data.get('id', '')
        stripe_sub_id = data.get('subscription', '')

        Invoice.objects.filter(stripe_invoice_id=stripe_invoice_id).update(status='open')
        if stripe_sub_id:
            Subscription.objects.filter(
                stripe_subscription_id=stripe_sub_id
            ).update(status='past_due')

    def _handle_subscription_updated(self, data) -> None:
        stripe_sub_id = data.get('id', '')
        items = data.get('items', {}).get('data', [])
        new_plan = _extract_plan_from_items(items)

        updates = {
            'status': data.get('status', 'active'),
            'cancel_at_period_end': data.get('cancel_at_period_end', False),
        }
        if new_plan:
            updates['plan'] = new_plan

        period_start = data.get('current_period_start')
        period_end = data.get('current_period_end')
        if period_start:
            updates['current_period_start'] = _ts_to_dt(period_start)
        if period_end:
            updates['current_period_end'] = _ts_to_dt(period_end)

        Subscription.objects.filter(stripe_subscription_id=stripe_sub_id).update(**updates)

    def _handle_subscription_deleted(self, data) -> None:
        stripe_sub_id = data.get('id', '')
        subscriptions = Subscription.objects.filter(
            stripe_subscription_id=stripe_sub_id
        ).select_related('tenant')

        for sub in subscriptions:
            sub.status = 'canceled'
            sub.plan = 'free'
            sub.save(update_fields=['status', 'plan', 'updated_at'])
            sub.tenant.plan = 'free'
            sub.tenant.save(update_fields=['plan', 'updated_at'])


def _extract_plan_from_items(items: list) -> str | None:
    """Extract plan name from Stripe subscription items price metadata."""
    for item in items:
        price = item.get('price', {})
        metadata = price.get('metadata', {})
        plan = metadata.get('plan')
        if plan:
            return plan
    return None


class PaymentMethodView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tenant = _get_tenant(request)
        if not tenant:
            return Response(
                {'error': {'code': 'tenant_not_found', 'message': 'Tenant not found.'}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        payment_methods = PaymentMethod.objects.filter(tenant=tenant).order_by(
            '-is_default', '-created_at'
        )
        serializer = PaymentMethodSerializer(payment_methods, many=True)
        return Response({'payment_methods': serializer.data})


class PaymentMethodCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        tenant = _get_tenant(request)
        if not tenant:
            return Response(
                {'error': {'code': 'tenant_not_found', 'message': 'Tenant not found.'}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        pm_id = request.data.get('stripe_payment_method_id', '')
        set_default = request.data.get('set_default', True)

        if not pm_id:
            return Response(
                {
                    'error': {
                        'code': 'validation_error',
                        'message': 'stripe_payment_method_id is required.',
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        subscription, _ = Subscription.objects.get_or_create(
            tenant=tenant,
            defaults={'plan': 'free', 'status': 'trialing'},
        )

        stripe_client = StripeClient()
        try:
            # Ensure customer exists
            if not subscription.stripe_customer_id:
                customer_id = stripe_client.create_customer(tenant)
                subscription.stripe_customer_id = customer_id
                subscription.save(update_fields=['stripe_customer_id', 'updated_at'])

            # Attach payment method to customer
            stripe_client.attach_payment_method(subscription.stripe_customer_id, pm_id)

            # Optionally set as default
            if set_default:
                stripe_client.set_default_payment_method(
                    subscription.stripe_customer_id, pm_id
                )

            # Retrieve payment method details from Stripe
            pm_data = stripe.PaymentMethod.retrieve(pm_id)
            card = pm_data.get('card', {})

            payment_method = PaymentMethod.objects.create(
                tenant=tenant,
                stripe_payment_method_id=pm_id,
                type=pm_data.get('type', 'card'),
                brand=card.get('brand', ''),
                last4=card.get('last4', ''),
                exp_month=card.get('exp_month'),
                exp_year=card.get('exp_year'),
                is_default=bool(set_default),
            )

        except stripe.error.StripeError as e:
            logger.error('Stripe error attaching payment method: %s', str(e))
            return Response(
                {'error': {'code': 'stripe_error', 'message': str(e)}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {'payment_method': PaymentMethodSerializer(payment_method).data},
            status=status.HTTP_201_CREATED,
        )
