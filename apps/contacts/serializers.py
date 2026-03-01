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

    class Meta:
        model = Contact
        fields = [
            'id', 'first_name', 'last_name', 'email', 'phone',
            'company', 'job_title', 'notes', 'group', 'group_name',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'group_name', 'created_at', 'updated_at']


class ContactCreateUpdateSerializer(serializers.Serializer):
    first_name = serializers.CharField(max_length=100)
    last_name = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    email = serializers.EmailField(required=False, allow_blank=True, default='')
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True, default='')
    company = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    job_title = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    notes = serializers.CharField(required=False, allow_blank=True, default='')
    group = serializers.UUIDField(required=False, allow_null=True, default=None)
