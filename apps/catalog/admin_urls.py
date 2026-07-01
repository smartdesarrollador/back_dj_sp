from django.urls import path

from .views import AdminCatalogDetailView, AdminCatalogListCreateView, AdminCatalogReorderView

urlpatterns = [
    path('', AdminCatalogListCreateView.as_view(), name='admin-catalog-list-create'),
    path('<uuid:pk>/', AdminCatalogDetailView.as_view(), name='admin-catalog-detail'),
    path('<uuid:pk>/reorder/', AdminCatalogReorderView.as_view(), name='admin-catalog-reorder'),
]
