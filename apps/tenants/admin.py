from django.contrib import admin

from apps.tenants.models import Tenant


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'plan', 'is_active', 'created_at']
    list_filter = ['plan', 'is_active']
    search_fields = ['name', 'slug', 'subdomain']
    readonly_fields = ['id', 'created_at', 'updated_at']
    ordering = ['name']
