from django.urls import path

from apps.snippets.views import CodeSnippetDetailView, CodeSnippetListCreateView

urlpatterns = [
    path('', CodeSnippetListCreateView.as_view(), name='snippet-list-create'),
    path('<uuid:pk>/', CodeSnippetDetailView.as_view(), name='snippet-detail'),
]
