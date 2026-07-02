from django.urls import path

from .views import HubActiveAnnouncementView, HubAnnouncementsTopView

urlpatterns = [
    path('active/', HubActiveAnnouncementView.as_view(), name='hub-announcement-active'),
    path('top/', HubAnnouncementsTopView.as_view(), name='hub-announcements-top'),
]
