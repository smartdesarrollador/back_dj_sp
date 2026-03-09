from django.contrib import admin

from .models import Service, TenantService


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ['slug', 'name', 'min_plan', 'is_active']
    list_filter = ['min_plan', 'is_active']
    search_fields = ['slug', 'name']


@admin.register(TenantService)
class TenantServiceAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'service', 'status', 'acquired_at']
    list_filter = ['status']
    search_fields = ['tenant__name', 'service__slug']
