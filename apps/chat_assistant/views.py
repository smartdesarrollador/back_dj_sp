"""
Chat Assistant views.

Public endpoints (no auth, no tenant):
  POST /api/v1/public/chat/session/   → create or resume a chat session
  POST /api/v1/public/chat/message/   → send a message, get SSE stream back

Admin endpoints (JWT auth + knowledge_base.manage permission):
  GET/POST /api/v1/admin/knowledge-base/
  GET/PATCH/DELETE /api/v1/admin/knowledge-base/<pk>/
  POST /api/v1/admin/knowledge-base/<pk>/toggle/
"""
import secrets

from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.rbac.permissions import HasPermission
from core.mixins import AuditMixin

from .models import ChatKnowledgeArticle, ChatMessage, ChatSession
from .serializers import (
    ChatKnowledgeArticleSerializer,
    ChatKnowledgeArticleWriteSerializer,
    ChatMessageInputSerializer,
    ChatMessageSerializer,
    ChatSessionInputSerializer,
    ChatSessionSerializer,
)
from .services import MAX_MESSAGES_PER_SESSION, stream_chat_response
from .throttles import ChatRateThrottle


# ─── Public views ─────────────────────────────────────────────────────────────

class ChatSessionView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ChatRateThrottle]

    @extend_schema(
        tags=['public-chat'],
        summary='Create or resume a chat session',
        request=ChatSessionInputSerializer,
        responses={200: ChatSessionSerializer},
        auth=[],
    )
    def post(self, request) -> Response:
        serializer = ChatSessionInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        token = serializer.validated_data.get('session_token') or secrets.token_hex(32)
        session, _ = ChatSession.objects.get_or_create(
            session_token=token,
            defaults={
                'ip_address': request.META.get('REMOTE_ADDR'),
                'user_agent': request.META.get('HTTP_USER_AGENT', '')[:500],
            },
        )
        return Response(ChatSessionSerializer(session).data)


class ChatMessageView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ChatRateThrottle]

    @extend_schema(
        tags=['public-chat'],
        summary='Send a chat message and receive a streaming response',
        request=ChatMessageInputSerializer,
        auth=[],
    )
    def post(self, request) -> Response | StreamingHttpResponse:
        serializer = ChatMessageInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        session = get_object_or_404(
            ChatSession,
            session_token=serializer.validated_data['session_token'],
        )

        if session.message_count >= MAX_MESSAGES_PER_SESSION:
            return Response(
                {'error': {'code': 'session_limit', 'message': 'Límite de mensajes alcanzado.'}},
                status=429,
            )

        user_message: str = serializer.validated_data['message']
        ChatMessage.objects.create(session=session, role='user', content=user_message)
        session.message_count += 1
        session.save(update_fields=['message_count', 'last_activity_at'])

        response = StreamingHttpResponse(
            stream_chat_response(session, user_message),
            content_type='text/event-stream; charset=utf-8',
        )
        response['Cache-Control'] = 'no-cache'
        response['X-Accel-Buffering'] = 'no'
        return response


class ChatHistoryView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        tags=['public-chat'],
        summary='Get message history for a session',
        parameters=[OpenApiParameter('session_token', str, OpenApiParameter.QUERY)],
        responses={200: ChatMessageSerializer(many=True)},
        auth=[],
    )
    def get(self, request) -> Response:
        token = request.query_params.get('session_token', '')
        session = get_object_or_404(ChatSession, session_token=token)
        messages = session.messages.order_by('created_at')
        return Response({'messages': ChatMessageSerializer(messages, many=True).data})


# ─── Admin views ──────────────────────────────────────────────────────────────

class KnowledgeArticleListCreateView(AuditMixin, APIView):
    permission_classes = [HasPermission('knowledge_base.manage')]

    @extend_schema(tags=['admin-chat'], summary='List knowledge base articles')
    def get(self, request) -> Response:
        qs = ChatKnowledgeArticle.objects.all()
        if category := request.query_params.get('category'):
            qs = qs.filter(category=category)
        if (is_active := request.query_params.get('is_active')) is not None:
            qs = qs.filter(is_active=is_active.lower() == 'true')
        return Response({'articles': ChatKnowledgeArticleSerializer(qs, many=True).data})

    @extend_schema(tags=['admin-chat'], summary='Create a knowledge base article')
    def post(self, request) -> Response:
        serializer = ChatKnowledgeArticleWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        article = ChatKnowledgeArticle.objects.create(**serializer.validated_data)
        self.log_action(
            request,
            action='kb_article_created',
            resource_type='chat_knowledge_article',
            resource_id=str(article.pk),
        )
        return Response(ChatKnowledgeArticleSerializer(article).data, status=201)


class KnowledgeArticleDetailView(AuditMixin, APIView):
    permission_classes = [HasPermission('knowledge_base.manage')]

    def _get_article(self, pk) -> ChatKnowledgeArticle:
        return get_object_or_404(ChatKnowledgeArticle, pk=pk)

    @extend_schema(tags=['admin-chat'], summary='Get a knowledge base article')
    def get(self, request, pk) -> Response:
        return Response(ChatKnowledgeArticleSerializer(self._get_article(pk)).data)

    @extend_schema(tags=['admin-chat'], summary='Update a knowledge base article')
    def patch(self, request, pk) -> Response:
        article = self._get_article(pk)
        serializer = ChatKnowledgeArticleWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        for field, value in serializer.validated_data.items():
            setattr(article, field, value)
        article.save()
        self.log_action(
            request,
            action='kb_article_updated',
            resource_type='chat_knowledge_article',
            resource_id=str(article.pk),
        )
        return Response(ChatKnowledgeArticleSerializer(article).data)

    @extend_schema(tags=['admin-chat'], summary='Delete a knowledge base article')
    def delete(self, request, pk) -> Response:
        article = self._get_article(pk)
        self.log_action(
            request,
            action='kb_article_deleted',
            resource_type='chat_knowledge_article',
            resource_id=str(article.pk),
        )
        article.delete()
        return Response(status=204)


class KnowledgeArticleToggleView(AuditMixin, APIView):
    permission_classes = [HasPermission('knowledge_base.manage')]

    @extend_schema(tags=['admin-chat'], summary='Toggle article active status')
    def post(self, request, pk) -> Response:
        article = get_object_or_404(ChatKnowledgeArticle, pk=pk)
        article.is_active = not article.is_active
        article.save(update_fields=['is_active'])
        self.log_action(
            request,
            action='kb_article_toggled',
            resource_type='chat_knowledge_article',
            resource_id=str(article.pk),
            extra={'is_active': article.is_active},
        )
        return Response({'is_active': article.is_active})
