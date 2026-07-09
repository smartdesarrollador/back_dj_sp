from django.urls import path

from apps.snippets.views import CodeSnippetDetailView, CodeSnippetListCreateView, SnippetTagsView

urlpatterns = [
    path('', CodeSnippetListCreateView.as_view(), name='snippet-list-create'),
    path('tags/', SnippetTagsView.as_view(), name='snippet-tags'),
    path('<uuid:pk>/', CodeSnippetDetailView.as_view(), name='snippet-detail'),
]
