from django.contrib import admin

from apps.contact.models import ContactMessage


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    list_display  = ['name', 'email', 'status', 'created_at']
    list_filter   = ['status']
    search_fields = ['name', 'email']
    readonly_fields = ['id', 'ip_address', 'created_at', 'updated_at']
