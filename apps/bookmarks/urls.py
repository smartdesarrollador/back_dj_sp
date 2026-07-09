from django.urls import path

from apps.bookmarks.views import (
    BookmarkCollectionDetailView,
    BookmarkCollectionListCreateView,
    BookmarkDetailView,
    BookmarkImportView,
    BookmarkListCreateView,
    BookmarkTagsView,
)

urlpatterns = [
    path('', BookmarkListCreateView.as_view(), name='bookmark-list-create'),
    path('import/', BookmarkImportView.as_view(), name='bookmark-import'),
    path('tags/', BookmarkTagsView.as_view(), name='bookmark-tags'),
    path('collections/', BookmarkCollectionListCreateView.as_view(), name='bookmark-collection-list-create'),
    path('collections/<uuid:pk>/', BookmarkCollectionDetailView.as_view(), name='bookmark-collection-detail'),
    path('<uuid:pk>/', BookmarkDetailView.as_view(), name='bookmark-detail'),
]
