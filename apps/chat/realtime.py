"""
Realtime helpers — broadcast chat events over the Channels layer.

All functions are safe no-ops when no channel layer is configured (e.g. in unit
tests that don't spin up Redis), so the REST flow never breaks.
"""
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


def conversation_group(conversation_id) -> str:
    return f'chat_conv_{conversation_id}'


def presence_group(user_id) -> str:
    return f'chat_presence_{user_id}'


def _send(group: str, payload: dict) -> None:
    layer = get_channel_layer()
    if layer is None:
        return
    try:
        async_to_sync(layer.group_send)(group, payload)
    except Exception:
        pass  # Realtime delivery is best-effort; never block the request


def broadcast_message(conversation_id, message_data: dict) -> None:
    _send(conversation_group(conversation_id), {'type': 'chat_message', 'message': message_data})


def broadcast_typing(conversation_id, user_id, user_name: str) -> None:
    _send(
        conversation_group(conversation_id),
        {'type': 'chat_typing', 'conversation': str(conversation_id),
         'user_id': str(user_id), 'user_name': user_name},
    )


def broadcast_membership_changed(conversation_id, member_user_ids) -> None:
    """
    Tell each member (via their presence group) to subscribe to a new/updated
    conversation. Their connected consumer joins the conversation group and the
    client refetches its conversation list.
    """
    payload = {'type': 'chat_membership', 'conversation': str(conversation_id)}
    for uid in member_user_ids:
        _send(presence_group(uid), payload)
