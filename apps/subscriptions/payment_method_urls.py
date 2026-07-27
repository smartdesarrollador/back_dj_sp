from django.urls import path

from apps.subscriptions.payment_method_views import (
    AdminPaymentMethodDetailView,
    AdminPaymentMethodListView,
)

urlpatterns = [
    path('methods/', AdminPaymentMethodListView.as_view(), name='admin-payment-methods'),
    path(
        'methods/<str:method>/',
        AdminPaymentMethodDetailView.as_view(),
        name='admin-payment-method-detail',
    ),
]
