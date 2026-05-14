from django.urls import path

from apps.releases.views import DesktopReleaseDownloadView, LatestReleaseView

urlpatterns = [
    path('latest/', LatestReleaseView.as_view(), name='public-release-latest'),
    path('download/<uuid:pk>/', DesktopReleaseDownloadView.as_view(), name='public-release-download'),
]
