from django.urls import path

from apps.releases.views import ReleaseDetailView, ReleaseListView

urlpatterns = [
    path('', ReleaseListView.as_view(), name='admin-release-list'),
    path('<uuid:pk>/', ReleaseDetailView.as_view(), name='admin-release-detail'),
]
