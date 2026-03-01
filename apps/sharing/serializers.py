"""
Sharing serializers.

  ShareSerializer         — read: full share details with user info
  ShareCreateSerializer   — write: resolve shared_with by email
  SharedWithMeSerializer  — read: extends ShareSerializer for recipient view
"""
from rest_framework import serializers

from apps.sharing.models import Share


class ShareSerializer(serializers.ModelSerializer):
    shared_with_email = serializers.EmailField(source='shared_with.email', read_only=True)
    shared_with_name = serializers.CharField(source='shared_with.name', read_only=True)
    shared_by_email = serializers.EmailField(source='shared_by.email', read_only=True)

    class Meta:
        model = Share
        fields = [
            'id',
            'resource_type',
            'resource_id',
            'shared_by_email',
            'shared_with_email',
            'shared_with_name',
            'permission_level',
            'is_inherited',
            'expires_at',
            'created_at',
        ]
        read_only_fields = fields


class ShareCreateSerializer(serializers.Serializer):
    resource_type = serializers.ChoiceField(choices=Share.RESOURCE_TYPES)
    resource_id = serializers.UUIDField()
    shared_with_email = serializers.EmailField()
    permission_level = serializers.ChoiceField(
        choices=Share.PERMISSION_LEVELS, default='viewer'
    )
    expires_at = serializers.DateTimeField(required=False, allow_null=True)


class SharedWithMeSerializer(ShareSerializer):
    """Serializer for the recipient's view — same fields as ShareSerializer."""
    class Meta(ShareSerializer.Meta):
        pass
