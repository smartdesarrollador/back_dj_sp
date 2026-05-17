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


def _fetch_resource_name(resource_type: str, resource_id) -> str:
    """Fallback single-object lookup when no batch cache is provided (e.g. in tests)."""
    try:
        if resource_type == 'snippet':
            from apps.snippets.models import CodeSnippet
            return CodeSnippet.objects.values_list('title', flat=True).get(pk=resource_id)
        if resource_type == 'note':
            from apps.notes.models import Note
            return Note.objects.values_list('title', flat=True).get(pk=resource_id)
        if resource_type == 'contact':
            from apps.contacts.models import Contact
            c = Contact.objects.values('first_name', 'last_name').get(pk=resource_id)
            return f"{c['first_name']} {c['last_name']}".strip()
        if resource_type == 'project':
            from apps.projects.models import Project
            return Project.objects.values_list('name', flat=True).get(pk=resource_id)
    except Exception:
        pass
    return ''


class SharedWithMeSerializer(ShareSerializer):
    """Recipient's view — enriched with resource_name, shared_by_name, access_level."""
    resource_name = serializers.SerializerMethodField()
    shared_by_name = serializers.CharField(source='shared_by.name', read_only=True)
    access_level = serializers.CharField(source='permission_level', read_only=True)

    class Meta(ShareSerializer.Meta):
        fields = ShareSerializer.Meta.fields + ['resource_name', 'shared_by_name', 'access_level']
        read_only_fields = fields

    def get_resource_name(self, obj) -> str:
        cache = self.context.get('resource_cache', {})
        cached = cache.get((obj.resource_type, obj.resource_id))
        return cached if cached is not None else _fetch_resource_name(obj.resource_type, obj.resource_id)
