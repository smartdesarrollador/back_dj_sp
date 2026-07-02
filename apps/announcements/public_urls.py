from django.urls import path

from .views import PublicActiveAnnouncementView

urlpatterns = [
    path('active/', PublicActiveAnnouncementView.as_view(), name='public-announcement-active'),
]
