from django.urls import path

from apps.tasks.views import (
    TaskBoardDetailView,
    TaskBoardListCreateView,
    TaskCommentListCreateView,
    TaskDetailView,
    TaskListCreateView,
    TaskReorderView,
)

urlpatterns = [
    path('boards/', TaskBoardListCreateView.as_view(), name='taskboard-list-create'),
    path('boards/<uuid:pk>/', TaskBoardDetailView.as_view(), name='taskboard-detail'),
    path('', TaskListCreateView.as_view(), name='task-list-create'),
    path('reorder/', TaskReorderView.as_view(), name='task-reorder'),
    path('<uuid:pk>/', TaskDetailView.as_view(), name='task-detail'),
    path('<uuid:task_pk>/comments/', TaskCommentListCreateView.as_view(), name='task-comment-list-create'),
]
