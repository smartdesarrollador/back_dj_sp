"""
Serializers for the Contacts module.
"""
from rest_framework import serializers

from apps.contacts.models import Contact, ContactGroup


class ContactGroupSerializer(serializers.ModelSerializer):
    contacts_count = serializers.SerializerMethodField()

    class Meta:
        model = ContactGroup
        fields = ['id', 'name', 'color', 'contacts_count', 'created_at', 'updated_at']
        read_only_fields = ['id', 'contacts_count', 'created_at', 'updated_at']

    def get_contacts_count(self, obj) -> int:
        return obj.contacts.count()


class ContactSerializer(serializers.ModelSerializer):
    group_name = serializers.CharField(source='group.name', read_only=True, default=None)
    name = serializers.SerializerMethodField()
    group = serializers.SerializerMethodField()
    is_shared = serializers.SerializerMethodField()
    shared_by_name = serializers.SerializerMethodField()

    class Meta:
        model = Contact
        fields = [
            'id', 'name', 'first_name', 'last_name', 'email', 'phone',
            'company', 'job_title', 'notes', 'group', 'group_name',
            'is_shared', 'shared_by_name', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'name', 'group_name', 'created_at', 'updated_at']

    def get_name(self, obj) -> str:
        return f'{obj.first_name} {obj.last_name}'.strip()

    def get_group(self, obj):
        if obj.group:
            return {'id': str(obj.group.id), 'name': obj.group.name, 'color': obj.group.color, 'contacts_count': 0}
        return None

    def get_is_shared(self, obj) -> bool:
        request = self.context.get('request')
        return bool(request) and obj.user_id != request.user.id

    def get_shared_by_name(self, obj) -> str | None:
        return self.context.get('shared_by_map', {}).get(obj.id)


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
