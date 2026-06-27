"""
Chat models — direct/group conversations and cross-tenant connections.

Authorization is always membership-based (ConversationMember), not tenant-only.
Conversation.tenant is nullable: intra-tenant threads keep the creator's tenant,
cross-tenant threads (Phase 2) leave it null. Cross-tenant chats require an
accepted ChatConnection between the two users.
"""
from django.conf import settings
from django.db import models

from core.models import BaseModel


def connected_user_ids(user) -> set:
    """IDs of users with an accepted ChatConnection to ``user`` (either direction)."""
    qs = ChatConnection.objects.filter(status='accepted').filter(
        models.Q(requester=user) | models.Q(addressee=user)
    )
    ids: set = set()
    for conn in qs.values('requester_id', 'addressee_id'):
        ids.add(conn['requester_id'])
        ids.add(conn['addressee_id'])
    ids.discard(user.id)
    return ids


class Conversation(BaseModel):
    TYPE_CHOICES = [
        ('direct', 'Direct'),
        ('group', 'Group'),
        ('self', 'Self'),
    ]

    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='conversations',
        null=True,
        blank=True,
    )
    type = models.CharField(max_length=10, choices=TYPE_CHOICES, default='direct')
    name = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='conversations_created',
    )
    avatar_color = models.CharField(max_length=20, default='blue')

    class Meta:
        db_table = 'chat_conversations'
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['tenant', 'type']),
        ]

    def __str__(self) -> str:
        return self.name or f'{self.type} {self.id}'


class ConversationMember(BaseModel):
    ROLE_CHOICES = [
        ('owner', 'Owner'),
        ('admin', 'Admin'),
        ('member', 'Member'),
    ]

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name='members',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='conversation_memberships',
    )
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default='member')
    last_read_at = models.DateTimeField(null=True, blank=True)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'chat_conversation_members'
        unique_together = [('conversation', 'user')]
        indexes = [
            models.Index(fields=['conversation', 'user']),
        ]

    def __str__(self) -> str:
        return f'{self.user_id} in {self.conversation_id}'


class Message(BaseModel):
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name='messages',
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='chat_messages',
    )
    content = models.TextField()
    reply_to = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='replies',
    )
    edited_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'chat_messages'
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['conversation', 'created_at']),
        ]

    def __str__(self) -> str:
        return f'msg {self.id} from {self.sender_id}'


class MessageAttachment(BaseModel):
    KIND_CHOICES = [
        ('image', 'Image'),
        ('file', 'File'),
    ]

    message = models.ForeignKey(
        Message,
        on_delete=models.CASCADE,
        related_name='attachments',
    )
    file = models.FileField(upload_to='chat_attachments/%Y/%m/')
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default='file')
    original_name = models.CharField(max_length=255, blank=True)
    size = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'chat_message_attachments'
        ordering = ['created_at']

    def __str__(self) -> str:
        return self.original_name or str(self.file)


class ChatConnection(BaseModel):
    """
    A cross-tenant (or intra-tenant) connection between two users. A user may
    only open a direct chat with someone they are ``accepted``-connected with.
    """
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('accepted', 'Accepted'),
        ('blocked', 'Blocked'),
    ]

    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='chat_connections_sent',
    )
    addressee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='chat_connections_received',
        null=True,
        blank=True,
    )
    invited_email = models.EmailField(blank=True)
    requester_tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='chat_connections_as_requester',
        null=True,
        blank=True,
    )
    addressee_tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='chat_connections_as_addressee',
        null=True,
        blank=True,
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'chat_connections'
        unique_together = [('requester', 'addressee')]
        indexes = [
            models.Index(fields=['addressee', 'status']),
            models.Index(fields=['requester', 'status']),
        ]

    def __str__(self) -> str:
        return f'{self.requester_id} → {self.addressee_id} ({self.status})'
