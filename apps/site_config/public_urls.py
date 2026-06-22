from django.urls import path

from apps.site_config.views import FooterPublicView

urlpatterns = [
    path('footer/', FooterPublicView.as_view()),
]
