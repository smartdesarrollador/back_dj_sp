from django.urls import path

from .views import KnowledgeArticleDetailView, KnowledgeArticleListCreateView, KnowledgeArticleToggleView

urlpatterns = [
    path('', KnowledgeArticleListCreateView.as_view()),
    path('<uuid:pk>/', KnowledgeArticleDetailView.as_view()),
    path('<uuid:pk>/toggle/', KnowledgeArticleToggleView.as_view()),
]
