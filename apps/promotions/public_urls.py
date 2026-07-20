from django.urls import path

from .public_views import PromotionValidateView

urlpatterns = [
    path('validate/', PromotionValidateView.as_view(), name='public-promotion-validate'),
]
