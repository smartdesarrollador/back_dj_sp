"""
Serializers for the Snippets module.
"""
from rest_framework import serializers

from apps.snippets.models import CodeSnippet


class CodeSnippetSerializer(serializers.ModelSerializer):
    class Meta:
        model = CodeSnippet
        fields = [
            'id', 'title', 'description', 'code', 'language',
            'tags', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class CodeSnippetCreateUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True, default='')
    code = serializers.CharField()
    language = serializers.ChoiceField(
        choices=CodeSnippet.LANGUAGE_CHOICES, required=False, default='other'
    )
    tags = serializers.ListField(
        child=serializers.CharField(max_length=50), required=False, default=list
    )
