"""Serializers for RBAC models: Permission, Role, RolePermission, UserRole."""
from rest_framework import serializers

from apps.rbac.models import Permission, Role, RolePermission, UserRole


class PermissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Permission
        fields = ['id', 'codename', 'name', 'resource', 'description']


class RolePermissionSerializer(serializers.ModelSerializer):
    codename = serializers.CharField(source='permission.codename', read_only=True)
    name = serializers.CharField(source='permission.name', read_only=True)

    class Meta:
        model = RolePermission
        fields = ['id', 'codename', 'name']


class RoleSerializer(serializers.ModelSerializer):
    permissions = RolePermissionSerializer(source='role_permissions', many=True, read_only=True)
    user_count = serializers.SerializerMethodField()

    class Meta:
        model = Role
        fields = ['id', 'name', 'description', 'is_system_role', 'permissions', 'user_count']

    def get_user_count(self, obj) -> int:
        return obj.user_roles.filter(user__is_active=True).count()


class RoleCreateUpdateSerializer(serializers.ModelSerializer):
    permission_ids = serializers.ListField(
        child=serializers.UUIDField(), write_only=True, required=False
    )

    class Meta:
        model = Role
        fields = ['name', 'description', 'permission_ids']


class UserRoleSerializer(serializers.ModelSerializer):
    role_id = serializers.UUIDField(write_only=True)
    role_name = serializers.CharField(source='role.name', read_only=True)

    class Meta:
        model = UserRole
        fields = ['id', 'role_id', 'role_name', 'created_at']
