from django.urls import path

from .views import PublicCatalogListView

urlpatterns = [
    path('', PublicCatalogListView.as_view(), name='public-catalog-list'),
]
