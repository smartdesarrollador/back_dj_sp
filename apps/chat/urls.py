from django.urls import path

from apps.chat.views import (
    ChatSearchView,
    ChatUsersView,
    ConnectionListCreateView,
    ConnectionRespondView,
    ConversationDetailView,
    ConversationListCreateView,
    GroupMemberView,
    MarkReadView,
    MessageConvertView,
    MessageDetailView,
    MessageListCreateView,
    SelfConversationView,
)

urlpatterns = [
    path('users/', ChatUsersView.as_view(), name='chat-users'),
    path('connections/', ConnectionListCreateView.as_view(), name='chat-connection-list'),
    path('connections/<uuid:pk>/respond/', ConnectionRespondView.as_view(), name='chat-connection-respond'),
    path('conversations/', ConversationListCreateView.as_view(), name='chat-conversation-list'),
    path('conversations/self/', SelfConversationView.as_view(), name='chat-conversation-self'),
    path('conversations/<uuid:pk>/', ConversationDetailView.as_view(), name='chat-conversation-detail'),
    path('conversations/<uuid:pk>/read/', MarkReadView.as_view(), name='chat-conversation-read'),
    path('conversations/<uuid:pk>/members/', GroupMemberView.as_view(), name='chat-conversation-members'),
    path('messages/', MessageListCreateView.as_view(), name='chat-message-list'),
    path('messages/<uuid:pk>/', MessageDetailView.as_view(), name='chat-message-detail'),
    path('messages/<uuid:pk>/convert/', MessageConvertView.as_view(), name='chat-message-convert'),
    path('search/', ChatSearchView.as_view(), name='chat-search'),
]
