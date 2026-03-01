"""
AuditLog serializer — read-only, expone info básica del usuario.
"""
from rest_framework import serializers

from apps.audit.models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source='user.email', read_only=True, default=None)
    user_name = serializers.CharField(source='user.name', read_only=True, default=None)

    class Meta:
        model = AuditLog
        fields = [
            'id', 'action', 'resource_type', 'resource_id',
            'user_email', 'user_name',
            'ip_address', 'user_agent', 'extra', 'created_at',
        ]
        read_only_fields = fields
