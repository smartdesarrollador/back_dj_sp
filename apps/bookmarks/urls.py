from django.urls import path

from apps.bookmarks.views import (
    BookmarkCollectionDetailView,
    BookmarkCollectionListCreateView,
    BookmarkDetailView,
    BookmarkListCreateView,
)

urlpatterns = [
    path('', BookmarkListCreateView.as_view(), name='bookmark-list-create'),
    path('collections/', BookmarkCollectionListCreateView.as_view(), name='bookmark-collection-list-create'),
    path('collections/<uuid:pk>/', BookmarkCollectionDetailView.as_view(), name='bookmark-collection-detail'),
    path('<uuid:pk>/', BookmarkDetailView.as_view(), name='bookmark-detail'),
]
