from django.urls import path

from .yape_public_views import YapeActivateView, YapeRejectView

urlpatterns = [
    path('activate/<str:token>/', YapeActivateView.as_view(), name='yape-activate'),
    path('reject/<str:token>/',   YapeRejectView.as_view(), name='yape-reject'),
]
