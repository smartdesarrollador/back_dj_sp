from django.contrib import admin

from .models import CatalogItem


@admin.register(CatalogItem)
class CatalogItemAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'is_active', 'order', 'created_at']
    list_filter = ['is_active', 'category']
    search_fields = ['name', 'short_description']
    ordering = ['order', 'created_at']
