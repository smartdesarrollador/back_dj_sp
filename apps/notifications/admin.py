from django.contrib import admin

from apps.notifications.models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ['title', 'category', 'tenant', 'read', 'created_at']
    list_filter = ['category', 'read']
    search_fields = ['title', 'message', 'tenant__slug']
    readonly_fields = ['id', 'created_at', 'updated_at']
