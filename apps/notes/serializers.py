"""
Serializers for the Notes module.
"""
from rest_framework import serializers

from apps.notes.models import Note


class NoteSerializer(serializers.ModelSerializer):
    tags = serializers.SerializerMethodField()
    is_shared = serializers.SerializerMethodField()
    shared_by_name = serializers.SerializerMethodField()

    class Meta:
        model = Note
        fields = [
            'id', 'title', 'content', 'category', 'is_pinned', 'color',
            'tags', 'is_shared', 'shared_by_name', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_tags(self, obj) -> list:
        return []

    def get_is_shared(self, obj) -> bool:
        request = self.context.get('request')
        return bool(request) and obj.user_id != request.user.id

    def get_shared_by_name(self, obj) -> str | None:
        return self.context.get('shared_by_map', {}).get(obj.id)


class NoteCreateUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255)
    content = serializers.CharField(required=False, allow_blank=True, default='')
    category = serializers.ChoiceField(
        choices=Note.CATEGORY_CHOICES, required=False, default='personal'
    )
    is_pinned = serializers.BooleanField(required=False, default=False)
    tags = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    color = serializers.CharField(required=False, max_length=20, default='gray')
