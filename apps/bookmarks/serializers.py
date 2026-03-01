"""
Serializers for the Bookmarks module.
"""
from rest_framework import serializers

from apps.bookmarks.models import Bookmark, BookmarkCollection


class BookmarkCollectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = BookmarkCollection
        fields = ['id', 'name', 'color', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class BookmarkSerializer(serializers.ModelSerializer):
    collection_name = serializers.CharField(source='collection.name', read_only=True, default=None)

    class Meta:
        model = Bookmark
        fields = [
            'id', 'url', 'title', 'description', 'tags', 'favicon_url',
            'collection', 'collection_name', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'collection_name', 'created_at', 'updated_at']


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
