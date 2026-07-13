"""
Serializers for the Notes module.
"""
from rest_framework import serializers

from apps.notes.models import Note, NoteCategory


class NoteCategorySerializer(serializers.ModelSerializer):
    notes_count = serializers.SerializerMethodField()

    class Meta:
        model = NoteCategory
        fields = ['id', 'name', 'color', 'notes_count', 'created_at', 'updated_at']
        read_only_fields = ['id', 'notes_count', 'created_at', 'updated_at']

    def get_notes_count(self, obj) -> int:
        return obj.notes.count()


class NoteSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True, default=None)
    category = serializers.SerializerMethodField()
    is_shared = serializers.SerializerMethodField()
    shared_by_name = serializers.SerializerMethodField()

    class Meta:
        model = Note
        fields = [
            'id', 'title', 'content', 'category', 'category_name', 'is_pinned', 'color',
            'tags', 'is_shared', 'shared_by_name', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'category_name', 'created_at', 'updated_at']

    def get_category(self, obj):
        if obj.category:
            return {
                'id': str(obj.category.id),
                'name': obj.category.name,
                'color': obj.category.color,
                'notes_count': 0,
            }
        return None

    def get_is_shared(self, obj) -> bool:
        request = self.context.get('request')
        return bool(request) and obj.user_id != request.user.id

    def get_shared_by_name(self, obj) -> str | None:
        return self.context.get('shared_by_map', {}).get(obj.id)


class NoteCreateUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255)
    content = serializers.CharField(required=False, allow_blank=True, default='')
    category = serializers.UUIDField(required=False, allow_null=True, default=None)
    is_pinned = serializers.BooleanField(required=False, default=False)
    tags = serializers.ListField(
        child=serializers.CharField(max_length=50, allow_blank=True), required=False, default=list
    )
    color = serializers.CharField(required=False, max_length=20, default='gray')

    def validate_tags(self, value):
        seen = set()
        normalized = []
        for raw in value:
            tag = raw.strip().lower()
            if not tag or tag in seen:
                continue
            seen.add(tag)
            normalized.append(tag)
        return normalized
