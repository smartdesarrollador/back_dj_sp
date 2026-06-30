from django.urls import path

from apps.notes.views import NoteDetailView, NoteListCreateView, NotePinView, NotesImportView

urlpatterns = [
    path('', NoteListCreateView.as_view(), name='note-list-create'),
    path('import/', NotesImportView.as_view(), name='note-import'),
    path('<uuid:pk>/', NoteDetailView.as_view(), name='note-detail'),
    path('<uuid:pk>/pin/', NotePinView.as_view(), name='note-pin'),
]
