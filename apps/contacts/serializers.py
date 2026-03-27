"""
Serializers for the Contacts module.
"""
from rest_framework import serializers

from apps.contacts.models import Contact, ContactGroup


class ContactGroupSerializer(serializers.ModelSerializer):
    class Meta:
        model = ContactGroup
        fields = ['id', 'name', 'color', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class ContactSerializer(serializers.ModelSerializer):
    group_name = serializers.CharField(source='group.name', read_only=True, default=None)
    name = serializers.SerializerMethodField()
    group = serializers.SerializerMethodField()

    class Meta:
        model = Contact
        fields = [
            'id', 'name', 'first_name', 'last_name', 'email', 'phone',
            'company', 'job_title', 'notes', 'group', 'group_name',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'name', 'group_name', 'created_at', 'updated_at']

    def get_name(self, obj) -> str:
        return f'{obj.first_name} {obj.last_name}'.strip()

    def get_group(self, obj):
        if obj.group:
            return {'id': str(obj.group.id), 'name': obj.group.name, 'color': obj.group.color, 'contacts_count': 0}
        return None


class ContactCreateUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200)
    first_name = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    last_name = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    email = serializers.EmailField(required=False, allow_blank=True, default='')
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True, default='')
    company = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    job_title = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    notes = serializers.CharField(required=False, allow_blank=True, default='')
    group = serializers.UUIDField(required=False, allow_null=True, default=None)
