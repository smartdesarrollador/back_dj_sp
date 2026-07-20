from django.urls import path

from .admin_views import (
    AdminPromotionDetailView,
    AdminPromotionListCreateView,
    AdminPromotionStatsView,
)

urlpatterns = [
    path('', AdminPromotionListCreateView.as_view(), name='admin-promotion-list'),
    path('<uuid:pk>/', AdminPromotionDetailView.as_view(), name='admin-promotion-detail'),
    path('<uuid:pk>/stats/', AdminPromotionStatsView.as_view(), name='admin-promotion-stats'),
]
