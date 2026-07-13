from django.urls import path

from apps.notes.views import (
    NoteCategoryDetailView,
    NoteCategoryListCreateView,
    NoteDetailView,
    NoteListCreateView,
    NotePinView,
    NotesImportView,
    NoteTagsView,
)

urlpatterns = [
    path('', NoteListCreateView.as_view(), name='note-list-create'),
    path('import/', NotesImportView.as_view(), name='note-import'),
    path('tags/', NoteTagsView.as_view(), name='note-tags'),
    path('categories/', NoteCategoryListCreateView.as_view(), name='note-category-list-create'),
    path('categories/<uuid:pk>/', NoteCategoryDetailView.as_view(), name='note-category-detail'),
    path('<uuid:pk>/', NoteDetailView.as_view(), name='note-detail'),
    path('<uuid:pk>/pin/', NotePinView.as_view(), name='note-pin'),
]
