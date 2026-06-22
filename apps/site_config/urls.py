from django.urls import path

from apps.site_config.views import FooterAdminView, FooterLinkCreateView, FooterLinkDetailView

urlpatterns = [
    path('', FooterAdminView.as_view()),
    path('links/', FooterLinkCreateView.as_view()),
    path('links/<int:pk>/', FooterLinkDetailView.as_view()),
]
