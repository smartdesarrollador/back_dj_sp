from django.urls import path
from django.views.decorators.csrf import csrf_exempt

from apps.subscriptions.views import (
    InvoiceListView,
    PaymentMethodCreateView,
    PaymentMethodView,
    WebhookView,
)

urlpatterns = [
    path('invoices', InvoiceListView.as_view(), name='invoice-list'),
    path('payment-methods', PaymentMethodView.as_view(), name='payment-method-list'),
    path('payment-methods/create', PaymentMethodCreateView.as_view(), name='payment-method-create'),
    path('webhooks', csrf_exempt(WebhookView.as_view()), name='stripe-webhook'),
]
