"""
Serializers for the Projects module.

Listing serializers intentionally omit `value` from fields (security).
Only RevealPasswordView returns decrypted values.
"""
from rest_framework import serializers

from apps.projects.models import (
    Project,
    ProjectItem,
    ProjectItemField,
    ProjectMember,
    ProjectSection,
)


class ProjectItemFieldSerializer(serializers.ModelSerializer):
    """Read-only field metadata — value is intentionally excluded from listings."""

    class Meta:
        model = ProjectItemField
        fields = ['id', 'label', 'field_type', 'is_encrypted', 'created_at', 'updated_at']
        read_only_fields = fields


class ProjectItemSerializer(serializers.ModelSerializer):
    fields = ProjectItemFieldSerializer(many=True, read_only=True)

    class Meta:
        model = ProjectItem
        fields = [
            'id', 'name', 'description', 'url', 'username', 'notes',
            'order', 'fields', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'fields', 'created_at', 'updated_at']


class ProjectSectionSerializer(serializers.ModelSerializer):
    items = ProjectItemSerializer(many=True, read_only=True)

    class Meta:
        model = ProjectSection
        fields = ['id', 'name', 'color', 'order', 'items', 'created_at', 'updated_at']
        read_only_fields = ['id', 'items', 'created_at', 'updated_at']


class ProjectSerializer(serializers.ModelSerializer):
    sections = ProjectSectionSerializer(many=True, read_only=True)
    member_count = serializers.SerializerMethodField()

    class Meta:
        model = Project
        fields = [
            'id', 'name', 'description', 'color', 'icon', 'is_archived',
            'member_count', 'sections', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'sections', 'member_count', 'created_at', 'updated_at']

    def get_member_count(self, obj) -> int:
        return obj.members.count()


class ProjectCreateUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = ['name', 'description', 'color', 'icon', 'is_archived']


class ProjectMemberSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source='user.email', read_only=True)
    user_name = serializers.CharField(source='user.name', read_only=True)

    class Meta:
        model = ProjectMember
        fields = ['id', 'user_email', 'user_name', 'role', 'created_at']
        read_only_fields = ['id', 'user_email', 'user_name', 'created_at']


class FieldCreateUpdateSerializer(serializers.ModelSerializer):
    """Used for create and update of ProjectItemField (accepts value)."""

    class Meta:
        model = ProjectItemField
        fields = ['label', 'value', 'field_type']
