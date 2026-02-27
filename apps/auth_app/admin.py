from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from apps.auth_app.models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ['email', 'name', 'tenant', 'is_active', 'email_verified', 'created_at']
    list_filter = ['is_active', 'email_verified', 'mfa_enabled', 'is_staff']
    search_fields = ['email', 'name']
    readonly_fields = ['id', 'created_at', 'updated_at', 'last_login']
    ordering = ['email']

    fieldsets = (
        (None, {'fields': ('id', 'email', 'password')}),
        ('Información personal', {'fields': ('name', 'avatar_url', 'tenant')}),
        ('Permisos', {
            'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions'),
        }),
        ('Email y MFA', {'fields': ('email_verified', 'mfa_enabled', 'mfa_secret')}),
        ('Fechas', {'fields': ('last_login', 'created_at', 'updated_at')}),
    )

    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'name', 'tenant', 'password1', 'password2'),
        }),
    )
