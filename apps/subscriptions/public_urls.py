from django.urls import path
from apps.subscriptions.views import PlansView

urlpatterns = [
    path('', PlansView.as_view(), name='public-plans'),
]
