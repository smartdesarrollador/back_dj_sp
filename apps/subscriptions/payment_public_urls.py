"""
Aprobación y rechazo de un comprobante desde los enlaces de un clic que llegan por
Telegram. Sin autenticación: la credencial es el `admin_token` de la URL.
"""
from django.urls import path

from .payment_public_views import ProofActivateView, ProofRejectView

urlpatterns = [
    path('activate/<str:token>/', ProofActivateView.as_view(), name='payment-proof-activate'),
    path('reject/<str:token>/',   ProofRejectView.as_view(),   name='payment-proof-reject'),
]
