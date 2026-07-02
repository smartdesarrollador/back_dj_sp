from django.contrib import admin

from .models import Announcement


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ['title', 'placement', 'is_active', 'priority', 'starts_at', 'ends_at', 'created_at']
    list_filter = ['is_active', 'placement']
    search_fields = ['title', 'message']
    ordering = ['-priority', '-created_at']
