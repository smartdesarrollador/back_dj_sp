"""
Django Admin registration for RBAC models.
"""
from django.contrib import admin

from .models import Permission, Role, RolePermission, UserRole


@admin.register(Permission)
class PermissionAdmin(admin.ModelAdmin):
    list_display = ['codename', 'name', 'resource', 'action', 'created_at']
    list_filter = ['resource']
    search_fields = ['codename', 'name']
    readonly_fields = ['id', 'created_at', 'updated_at']
    ordering = ['resource', 'action']


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ['name', 'tenant', 'is_system_role', 'inherits_from', 'created_at']
    list_filter = ['is_system_role', 'tenant']
    search_fields = ['name']
    readonly_fields = ['id', 'created_at', 'updated_at']


@admin.register(RolePermission)
class RolePermissionAdmin(admin.ModelAdmin):
    list_display = ['role', 'permission', 'scope', 'created_at']
    list_filter = ['scope', 'role']
    search_fields = ['role__name', 'permission__codename']
    readonly_fields = ['id', 'created_at']


@admin.register(UserRole)
class UserRoleAdmin(admin.ModelAdmin):
    list_display = ['user', 'role', 'assigned_by', 'assigned_at', 'expires_at']
    list_filter = ['role']
    search_fields = ['user__email', 'role__name']
    readonly_fields = ['id', 'created_at', 'updated_at', 'assigned_at']
