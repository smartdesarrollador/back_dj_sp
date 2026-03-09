from django.urls import path

from .views import ActiveServicesView, ServiceCatalogView

urlpatterns = [
    path('', ServiceCatalogView.as_view(), name='service-catalog'),
    path('active/', ActiveServicesView.as_view(), name='service-active'),
]
