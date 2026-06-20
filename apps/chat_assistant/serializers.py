from rest_framework import serializers

from .models import ChatKnowledgeArticle, ChatMessage, ChatSession


class ChatMessageInputSerializer(serializers.Serializer):
    session_token = serializers.CharField(max_length=64)
    message = serializers.CharField(max_length=2000, trim_whitespace=True)


class ChatSessionInputSerializer(serializers.Serializer):
    session_token = serializers.CharField(max_length=64, required=False, allow_blank=True)


class ChatSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatSession
        fields = ['session_token', 'message_count', 'created_at']


class ChatMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatMessage
        fields = ['id', 'role', 'content', 'created_at']
        read_only_fields = fields


class ChatKnowledgeArticleSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatKnowledgeArticle
        fields = [
            'id', 'title', 'content', 'category', 'keywords',
            'is_active', 'order', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class ChatKnowledgeArticleWriteSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=200)
    content = serializers.CharField()
    category = serializers.ChoiceField(choices=ChatKnowledgeArticle.CATEGORY_CHOICES)
    keywords = serializers.ListField(
        child=serializers.CharField(max_length=100),
        default=list,
        required=False,
    )
    order = serializers.IntegerField(default=0, min_value=0, required=False)
