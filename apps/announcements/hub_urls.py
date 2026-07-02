from django.urls import path

from .views import HubActiveAnnouncementView

urlpatterns = [
    path('active/', HubActiveAnnouncementView.as_view(), name='hub-announcement-active'),
]
