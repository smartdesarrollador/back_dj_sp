from django.urls import path

from apps.releases.views import LatestReleaseView

urlpatterns = [
    path('latest/', LatestReleaseView.as_view(), name='public-release-latest'),
]
