from django.urls import path
from apps.tenants.public_views import PublicBrandingView

urlpatterns = [
    path('', PublicBrandingView.as_view(), name='public-branding'),
]
