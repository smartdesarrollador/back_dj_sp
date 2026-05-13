from django.contrib import admin

from apps.releases.models import DesktopRelease


@admin.register(DesktopRelease)
class DesktopReleaseAdmin(admin.ModelAdmin):
    list_display = [
        'version', 'platform', 'file_name', 'file_size',
        'is_published', 'download_count', 'created_at',
    ]
    list_filter = ['platform', 'is_published']
    search_fields = ['version', 'file_name']
    readonly_fields = ['id', 'file_name', 'file_size', 'sha256', 'download_count', 'created_at', 'updated_at']
    ordering = ['-created_at']
