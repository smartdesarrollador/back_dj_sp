from django.urls import path

from .yape_public_views import YapeActivateView, YapeRejectView
from .yape_admin_views import YapeConfigPublicView

urlpatterns = [
    path('activate/<str:token>/', YapeActivateView.as_view(), name='yape-activate'),
    path('reject/<str:token>/',   YapeRejectView.as_view(), name='yape-reject'),
    path('config/',               YapeConfigPublicView.as_view(), name='yape-config-public'),
]
