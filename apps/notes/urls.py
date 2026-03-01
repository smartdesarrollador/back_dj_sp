from django.urls import path

from apps.notes.views import NoteDetailView, NoteListCreateView, NotePinView

urlpatterns = [
    path('', NoteListCreateView.as_view(), name='note-list-create'),
    path('<uuid:pk>/', NoteDetailView.as_view(), name='note-detail'),
    path('<uuid:pk>/pin/', NotePinView.as_view(), name='note-pin'),
]
