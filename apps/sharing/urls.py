from django.urls import path

from apps.sharing.views import (
    ShareDeleteView,
    ShareListCreateView,
    ShareUpdateView,
    SharedWithMeView,
)

urlpatterns = [
    path('', ShareListCreateView.as_view(), name='share-list-create'),
    path('shared-with-me/', SharedWithMeView.as_view(), name='shared-with-me'),
    path('<uuid:pk>/', ShareUpdateView.as_view(), name='share-update'),
    path('<uuid:pk>/delete/', ShareDeleteView.as_view(), name='share-delete'),
]
