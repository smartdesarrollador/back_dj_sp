from django.urls import path

from .views import AdminAnnouncementDetailView, AdminAnnouncementListCreateView

urlpatterns = [
    path('', AdminAnnouncementListCreateView.as_view(), name='admin-announcement-list-create'),
    path('<uuid:pk>/', AdminAnnouncementDetailView.as_view(), name='admin-announcement-detail'),
]
