from django.urls import path

from apps.ssl_certs.views import SSLCertificateDetailView, SSLCertificateListCreateView

urlpatterns = [
    path('', SSLCertificateListCreateView.as_view(), name='ssl-cert-list-create'),
    path('<uuid:pk>/', SSLCertificateDetailView.as_view(), name='ssl-cert-detail'),
]
