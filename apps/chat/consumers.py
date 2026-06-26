"""
ChatConsumer — one WebSocket per authenticated user. Subscribes to a group per
conversation the user belongs to, plus a personal presence group. Relays new
messages, typing indicators and online/offline presence.
"""
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from apps.chat.realtime import (
    broadcast_typing,
    conversation_group,
    presence_group,
)


class ChatConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.user = self.scope.get('user')
        if not self.user or not self.user.is_authenticated:
            await self.close(code=4401)
            return
        await self.accept()

        self.conversation_ids = await self._conversation_ids()
        for conv_id in self.conversation_ids:
            await self.channel_layer.group_add(conversation_group(conv_id), self.channel_name)
        await self.channel_layer.group_add(presence_group(self.user.id), self.channel_name)

        # Announce presence to everyone sharing a conversation with this user.
        await self._broadcast_presence(online=True)

    async def disconnect(self, code):
        if not getattr(self, 'user', None) or not self.user.is_authenticated:
            return
        for conv_id in getattr(self, 'conversation_ids', []):
            await self.channel_layer.group_discard(conversation_group(conv_id), self.channel_name)
        await self.channel_layer.group_discard(presence_group(self.user.id), self.channel_name)
        await self._broadcast_presence(online=False)

    async def receive_json(self, content, **kwargs):
        action = content.get('action')
        if action == 'typing':
            conv_id = content.get('conversation')
            if conv_id and str(conv_id) in {str(c) for c in self.conversation_ids}:
                broadcast_typing(conv_id, self.user.id, self.user.name)
        # 'ping' and unknown actions are ignored (keep-alive handled by the client).

    # ── group event handlers (type → method) ────────────────────────────────

    async def chat_message(self, event):
        await self.send_json({'event': 'message', 'message': event['message']})

    async def chat_typing(self, event):
        if str(event.get('user_id')) == str(self.user.id):
            return  # don't echo my own typing
        await self.send_json({
            'event': 'typing',
            'conversation': event['conversation'],
            'user_id': event['user_id'],
            'user_name': event['user_name'],
        })

    async def chat_presence(self, event):
        if str(event.get('user_id')) == str(self.user.id):
            return
        await self.send_json({
            'event': 'presence',
            'user_id': event['user_id'],
            'online': event['online'],
        })

    async def chat_membership(self, event):
        conv_id = str(event['conversation'])
        if conv_id not in {str(c) for c in self.conversation_ids}:
            self.conversation_ids.append(conv_id)
            await self.channel_layer.group_add(conversation_group(conv_id), self.channel_name)
        await self.send_json({'event': 'membership', 'conversation': conv_id})

    # ── helpers ──────────────────────────────────────────────────────────────

    @database_sync_to_async
    def _conversation_ids(self):
        from apps.chat.models import ConversationMember
        return [
            str(cid) for cid in ConversationMember.objects.filter(
                user=self.user
            ).values_list('conversation_id', flat=True)
        ]

    @database_sync_to_async
    def _peer_user_ids(self):
        from apps.chat.models import ConversationMember
        return [
            str(uid) for uid in ConversationMember.objects.filter(
                conversation_id__in=self.conversation_ids
            ).exclude(user=self.user).values_list('user_id', flat=True).distinct()
        ]

    async def _broadcast_presence(self, online: bool):
        peer_ids = await self._peer_user_ids()
        payload = {
            'type': 'chat_presence',
            'user_id': str(self.user.id),
            'online': online,
        }
        for uid in peer_ids:
            await self.channel_layer.group_send(presence_group(uid), payload)
