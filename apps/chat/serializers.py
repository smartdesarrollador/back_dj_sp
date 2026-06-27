"""
Serializers for the Chat module.
"""
from rest_framework import serializers

from apps.chat.models import ChatConnection, Conversation, ConversationMember, Message


class ChatUserSerializer(serializers.Serializer):
    """Lightweight representation of a user inside chat payloads."""

    id = serializers.UUIDField()
    name = serializers.CharField()
    email = serializers.EmailField()
    avatar_url = serializers.CharField(allow_blank=True)


class MessageSerializer(serializers.ModelSerializer):
    sender = ChatUserSerializer(read_only=True)
    reply_to = serializers.SerializerMethodField()
    content = serializers.SerializerMethodField()
    is_mine = serializers.SerializerMethodField()
    is_deleted = serializers.SerializerMethodField()
    attachments = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = [
            'id', 'conversation', 'sender', 'content', 'reply_to',
            'is_mine', 'is_deleted', 'attachments', 'edited_at', 'created_at',
        ]
        read_only_fields = fields

    def get_attachments(self, obj) -> list:
        request = self.context.get('request')
        result = []
        for att in obj.attachments.all():
            url = att.file.url
            if request is not None:
                url = request.build_absolute_uri(url)
            result.append({
                'id': str(att.id),
                'url': url,
                'kind': att.kind,
                'original_name': att.original_name,
                'size': att.size,
            })
        return result

    def get_content(self, obj) -> str:
        return '' if obj.deleted_at else obj.content

    def get_is_deleted(self, obj) -> bool:
        return obj.deleted_at is not None

    def get_is_mine(self, obj) -> bool:
        user = self.context.get('request').user if self.context.get('request') else None
        return bool(user and obj.sender_id == user.id)

    def get_reply_to(self, obj) -> dict | None:
        if not obj.reply_to_id:
            return None
        reply = obj.reply_to
        return {
            'id': str(reply.id),
            'content': '' if reply.deleted_at else reply.content[:120],
            'sender_name': reply.sender.name,
        }


class ChatConnectionSerializer(serializers.ModelSerializer):
    """Represents a connection from the current user's point of view."""

    other_user = serializers.SerializerMethodField()
    tenant_name = serializers.SerializerMethodField()
    direction = serializers.SerializerMethodField()

    class Meta:
        model = ChatConnection
        fields = ['id', 'status', 'direction', 'other_user', 'tenant_name', 'created_at']
        read_only_fields = fields

    def _current_user(self):
        request = self.context.get('request')
        return request.user if request else None

    def _other(self, obj):
        user = self._current_user()
        if user and obj.requester_id == user.id:
            return obj.addressee, obj.addressee_tenant
        return obj.requester, obj.requester_tenant

    def get_other_user(self, obj) -> dict:
        other, _ = self._other(obj)
        if other is None:
            # Pending email invite to an unregistered user.
            return {'id': '', 'name': obj.invited_email or 'Invitado', 'email': obj.invited_email, 'avatar_url': ''}
        return ChatUserSerializer(other).data

    def get_tenant_name(self, obj) -> str:
        _, tenant = self._other(obj)
        return tenant.name if tenant else ''

    def get_direction(self, obj) -> str:
        user = self._current_user()
        return 'outgoing' if (user and obj.requester_id == user.id) else 'incoming'


class ConversationMemberSerializer(serializers.ModelSerializer):
    user = ChatUserSerializer(read_only=True)

    class Meta:
        model = ConversationMember
        fields = ['id', 'user', 'role', 'joined_at']
        read_only_fields = fields


class ConversationListSerializer(serializers.ModelSerializer):
    display_name = serializers.SerializerMethodField()
    display_avatar = serializers.SerializerMethodField()
    last_message = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()
    member_count = serializers.SerializerMethodField()
    other_user_id = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = [
            'id', 'type', 'name', 'avatar_color', 'display_name', 'display_avatar',
            'last_message', 'unread_count', 'member_count', 'other_user_id', 'updated_at',
        ]
        read_only_fields = fields

    def get_other_user_id(self, obj) -> str | None:
        if obj.type != 'direct':
            return None
        other = self._other_member(obj)
        return str(other.user_id) if other else None

    def _current_user(self):
        request = self.context.get('request')
        return request.user if request else None

    def _other_member(self, obj):
        """For direct conversations, the member that is not the current user."""
        user = self._current_user()
        for member in obj.members.all():
            if not user or member.user_id != user.id:
                return member
        return None

    def get_display_name(self, obj) -> str:
        if obj.type == 'self':
            return 'Mensajes guardados'
        if obj.type == 'group':
            return obj.name or 'Grupo'
        other = self._other_member(obj)
        return other.user.name if other else 'Chat'

    def get_display_avatar(self, obj) -> dict:
        if obj.type == 'self':
            return {'type': 'self', 'name': 'Mensajes guardados', 'color': obj.avatar_color}
        if obj.type == 'group':
            return {'type': 'group', 'name': obj.name or 'Grupo', 'color': obj.avatar_color}
        other = self._other_member(obj)
        if other:
            return {
                'type': 'user',
                'name': other.user.name,
                'avatar_url': other.user.avatar_url,
                'color': obj.avatar_color,
            }
        return {'type': 'user', 'name': '?', 'avatar_url': '', 'color': obj.avatar_color}

    def get_member_count(self, obj) -> int:
        return obj.members.count()

    def get_last_message(self, obj) -> dict | None:
        last = max(
            (m for m in obj.messages.all()), key=lambda m: m.created_at, default=None
        )
        if not last:
            return None
        return {
            'id': str(last.id),
            'content': '' if last.deleted_at else last.content[:120],
            'sender_name': last.sender.name,
            'created_at': last.created_at,
        }

    def get_unread_count(self, obj) -> int:
        user = self._current_user()
        if not user:
            return 0
        member = next((m for m in obj.members.all() if m.user_id == user.id), None)
        if not member:
            return 0
        count = 0
        for message in obj.messages.all():
            if message.sender_id == user.id:
                continue
            if member.last_read_at is None or message.created_at > member.last_read_at:
                count += 1
        return count


class ConversationDetailSerializer(ConversationListSerializer):
    members = ConversationMemberSerializer(many=True, read_only=True)

    class Meta(ConversationListSerializer.Meta):
        fields = ConversationListSerializer.Meta.fields + ['members', 'created_at']
