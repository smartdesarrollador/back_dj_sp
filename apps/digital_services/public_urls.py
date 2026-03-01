from django.urls import path

from apps.digital_services.public_views import (
    PublicCVView,
    PublicLandingView,
    PublicPortfolioItemView,
    PublicPortfolioView,
    PublicProfileDetailView,
)

urlpatterns = [
    path('profiles/<slug:username>/', PublicProfileDetailView.as_view()),
    path('landing/<slug:username>/', PublicLandingView.as_view()),
    path('portafolio/<slug:username>/', PublicPortfolioView.as_view()),
    path('portafolio/<slug:username>/<slug:slug>/', PublicPortfolioItemView.as_view()),
    path('cv/<slug:username>/', PublicCVView.as_view()),
]
