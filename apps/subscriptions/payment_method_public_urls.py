from django.urls import path

from apps.subscriptions.payment_method_views import PublicPaymentMethodsView

urlpatterns = [
    path('', PublicPaymentMethodsView.as_view(), name='public-payment-methods'),
]
