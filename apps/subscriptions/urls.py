from django.urls import path
from django.views.decorators.csrf import csrf_exempt

from apps.subscriptions.views import (
    InvoiceListView,
    PaymentMethodDetailView,
    PaymentMethodListView,
    WebhookView,
)

urlpatterns = [
    path('invoices', InvoiceListView.as_view(), name='invoice-list'),
    path('payment-methods', PaymentMethodListView.as_view(), name='payment-method-list'),
    path('payment-methods/<uuid:pm_id>/', PaymentMethodDetailView.as_view(), name='payment-method-detail'),
    path('webhooks', csrf_exempt(WebhookView.as_view()), name='stripe-webhook'),
]
