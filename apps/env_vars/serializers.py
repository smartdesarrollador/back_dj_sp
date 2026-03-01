"""
Serializers for the EnvVars module.
"""
from rest_framework import serializers

from apps.env_vars.models import EnvVariable


class EnvVariableSerializer(serializers.ModelSerializer):
    """Read serializer — value field excluded for security."""

    class Meta:
        model = EnvVariable
        fields = [
            'id', 'key', 'environment', 'description',
            'is_encrypted', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'is_encrypted', 'created_at', 'updated_at']


class EnvVariableCreateUpdateSerializer(serializers.Serializer):
    key = serializers.CharField(max_length=100)
    value = serializers.CharField()
    environment = serializers.ChoiceField(
        choices=EnvVariable.ENVIRONMENT_CHOICES, required=False, default='all'
    )
    description = serializers.CharField(required=False, allow_blank=True, default='')
