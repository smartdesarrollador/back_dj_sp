from django.contrib import admin

from apps.chat.models import (
    ChatConnection,
    Conversation,
    ConversationMember,
    Message,
    MessageAttachment,
)


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ('id', 'type', 'name', 'tenant', 'created_by', 'updated_at')
    list_filter = ('type',)
    search_fields = ('name',)


@admin.register(ConversationMember)
class ConversationMemberAdmin(admin.ModelAdmin):
    list_display = ('id', 'conversation', 'user', 'role', 'last_read_at')
    list_filter = ('role',)


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ('id', 'conversation', 'sender', 'created_at', 'deleted_at')
    search_fields = ('content',)


@admin.register(MessageAttachment)
class MessageAttachmentAdmin(admin.ModelAdmin):
    list_display = ('id', 'message', 'kind', 'original_name', 'size', 'created_at')
    list_filter = ('kind',)


@admin.register(ChatConnection)
class ChatConnectionAdmin(admin.ModelAdmin):
    list_display = ('id', 'requester', 'addressee', 'invited_email', 'status', 'responded_at', 'created_at')
    list_filter = ('status',)
