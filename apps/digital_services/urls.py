from django.urls import path

from apps.digital_services.views import (
    CVExportPDFView,
    CVView,
    CustomDomainVerifyView,
    CustomDomainView,
    DigitalAnalyticsView,
    DigitalCardView,
    GenerateQRView,
    LandingView,
    PortfolioDetailView,
    PortfolioListCreateView,
    PublicProfileView,
)

urlpatterns = [
    path('profile/', PublicProfileView.as_view()),
    path('tarjeta/', DigitalCardView.as_view()),
    path('tarjeta/qr/', GenerateQRView.as_view()),
    path('landing/', LandingView.as_view()),
    path('portafolio/', PortfolioListCreateView.as_view()),
    path('portafolio/<uuid:pk>/', PortfolioDetailView.as_view()),
    path('cv/', CVView.as_view()),
    path('cv/export/', CVExportPDFView.as_view()),
    path('analytics/<str:service>/', DigitalAnalyticsView.as_view()),
    path('custom-domain/', CustomDomainView.as_view()),
    path('custom-domain/verify/', CustomDomainVerifyView.as_view()),
]
