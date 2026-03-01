"""
Serializers for the SSH Keys module.
"""
from rest_framework import serializers

from apps.ssh_keys.models import SSHKey


class SSHKeySerializer(serializers.ModelSerializer):
    """Read serializer — private_key excluded (write-only)."""

    class Meta:
        model = SSHKey
        fields = [
            'id', 'name', 'public_key', 'algorithm', 'fingerprint',
            'description', 'is_encrypted', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'fingerprint', 'is_encrypted', 'created_at', 'updated_at']


class SSHKeyCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    public_key = serializers.CharField()
    private_key = serializers.CharField(required=False, allow_blank=True, default='')
    algorithm = serializers.ChoiceField(
        choices=SSHKey.ALGORITHM_CHOICES, required=False, default='rsa'
    )
    description = serializers.CharField(required=False, allow_blank=True, default='')
