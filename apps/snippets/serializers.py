"""
Serializers for the Snippets module.
"""
from rest_framework import serializers

from apps.snippets.models import CodeSnippet


class CodeSnippetSerializer(serializers.ModelSerializer):
    is_shared = serializers.SerializerMethodField()
    shared_by_name = serializers.SerializerMethodField()

    class Meta:
        model = CodeSnippet
        fields = [
            'id', 'title', 'description', 'code', 'language', 'tags',
            'is_favorite', 'usage_count', 'is_shared', 'shared_by_name',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'usage_count', 'created_at', 'updated_at']

    def get_is_shared(self, obj) -> bool:
        request = self.context.get('request')
        return bool(request) and obj.user_id != request.user.id

    def get_shared_by_name(self, obj) -> str | None:
        return self.context.get('shared_by_map', {}).get(obj.id)


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
    is_favorite = serializers.BooleanField(required=False, default=False)
