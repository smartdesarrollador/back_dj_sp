from django.urls import path

from apps.subscriptions.currency_views import CurrencyPublicView

urlpatterns = [
    path('', CurrencyPublicView.as_view(), name='public-currency'),
]
