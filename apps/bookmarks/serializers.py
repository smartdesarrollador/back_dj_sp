"""
Serializers for the Bookmarks module.
"""
from rest_framework import serializers

from apps.bookmarks.models import Bookmark, BookmarkCollection


class BookmarkCollectionSerializer(serializers.ModelSerializer):
    bookmarks_count = serializers.SerializerMethodField()

    class Meta:
        model = BookmarkCollection
        fields = ['id', 'name', 'color', 'bookmarks_count', 'created_at', 'updated_at']
        read_only_fields = ['id', 'bookmarks_count', 'created_at', 'updated_at']

    def get_bookmarks_count(self, obj) -> int:
        return obj.bookmarks.count()


class BookmarkSerializer(serializers.ModelSerializer):
    collection_name = serializers.CharField(source='collection.name', read_only=True, default=None)
    collection = serializers.SerializerMethodField()

    class Meta:
        model = Bookmark
        fields = [
            'id', 'url', 'title', 'description', 'tags', 'favicon_url',
            'is_favorite', 'collection', 'collection_name', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'collection_name', 'created_at', 'updated_at']

    def get_collection(self, obj):
        if obj.collection:
            return {
                'id': str(obj.collection.id),
                'name': obj.collection.name,
                'color': obj.collection.color,
                'bookmarks_count': 0,
            }
        return None


class BookmarkCreateUpdateSerializer(serializers.Serializer):
    url = serializers.URLField(max_length=2048)
    title = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True, default='')
    tags = serializers.ListField(
        child=serializers.CharField(max_length=50),
        required=False,
        default=list,
    )
    favicon_url = serializers.URLField(required=False, allow_blank=True, default='')
    collection = serializers.UUIDField(required=False, allow_null=True, default=None)
    is_favorite = serializers.BooleanField(required=False, default=False)
