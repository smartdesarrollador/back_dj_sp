"""
Serializers for the Vault module.
"""
from rest_framework import serializers

from apps.vault.models import VaultItem


class VaultItemListSerializer(serializers.ModelSerializer):
    """List/metadata view — never exposes the ciphertext or decrypted data."""

    class Meta:
        model = VaultItem
        fields = ['id', 'title', 'item_type', 'favorite', 'created_at', 'updated_at']
        read_only_fields = fields


class VaultItemCreateUpdateSerializer(serializers.Serializer):
    """Input for create/update. `data` is the secret payload (object)."""

    title = serializers.CharField(max_length=255)
    item_type = serializers.ChoiceField(choices=VaultItem.ITEM_TYPES, default='login')
    data = serializers.DictField(required=True)
    favorite = serializers.BooleanField(required=False, default=False)


class MasterPasswordSetupSerializer(serializers.Serializer):
    master_password = serializers.CharField(min_length=8, max_length=128, write_only=True)


class MasterPasswordChangeSerializer(serializers.Serializer):
    current_master_password = serializers.CharField(write_only=True)
    new_master_password = serializers.CharField(min_length=8, max_length=128, write_only=True)


class UnlockSerializer(serializers.Serializer):
    master_password = serializers.CharField(write_only=True)


class RecoverSerializer(serializers.Serializer):
    recovery_code = serializers.CharField(write_only=True)
    new_master_password = serializers.CharField(min_length=8, max_length=128, write_only=True)
