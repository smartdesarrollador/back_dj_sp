from django.urls import path

from .views import ChatHistoryView, ChatMessageView, ChatSessionView

urlpatterns = [
    path('session/', ChatSessionView.as_view()),
    path('message/', ChatMessageView.as_view()),
    path('history/', ChatHistoryView.as_view()),
]
