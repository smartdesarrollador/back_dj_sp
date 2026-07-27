"""
Administración de pagos manuales: los métodos y su cola de comprobantes, bajo el mismo
prefijo `/api/v1/admin/payments/` que la sección «Pagos» del Admin Panel.
"""
from django.urls import path

from .payment_admin_views import ProofListView, ProofReviewView
from .payment_method_views import AdminPaymentMethodDetailView, AdminPaymentMethodListView

urlpatterns = [
    path('methods/', AdminPaymentMethodListView.as_view(), name='admin-payment-methods'),
    path(
        'methods/<str:method>/',
        AdminPaymentMethodDetailView.as_view(),
        name='admin-payment-method-detail',
    ),
    path('proofs/', ProofListView.as_view(), name='payment-proof-list'),
    path(
        'proofs/<uuid:proof_id>/review/',
        ProofReviewView.as_view(),
        name='payment-proof-review',
    ),
]
