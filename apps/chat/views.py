"""
Chat views — intra-tenant direct/group conversations and messages (Phase 1).

URL namespace: /api/v1/app/chat/

Authorization model: every queryset is scoped to conversations where the
requesting user is a ConversationMember. No global ``chat.*`` RBAC permission is
required (those are not seeded), so views use ``IsAuthenticated`` + membership
filtering — mirroring apps/support.
"""
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from drf_spectacular.types import OpenApiTypes
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from django.conf import settings
from django.core.mail import send_mail

from apps.chat.models import (
    ChatConnection,
    Conversation,
    ConversationMember,
    Message,
    MessageAttachment,
    connected_user_ids,
)
from apps.chat.serializers import (
    ChatConnectionSerializer,
    ChatUserSerializer,
    ConversationDetailSerializer,
    ConversationListSerializer,
    MessageSerializer,
)
from apps.chat.realtime import broadcast_membership_changed, broadcast_message
from apps.rbac.permissions import _user_has_permission, check_plan_limit
from core.mixins import AuditMixin

User = ConversationMember._meta.get_field('user').related_model

_NOT_FOUND = Response(
    {'error': {'code': 'not_found', 'message': 'Not found.'}}, status=404
)

_MESSAGES_PAGE_SIZE = 30
_MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 10 MB


def _member_qs(user):
    """Conversations the user belongs to, with members/messages prefetched."""
    return (
        Conversation.objects.filter(members__user=user)
        .prefetch_related(
            Prefetch('members', queryset=ConversationMember.objects.select_related('user')),
            Prefetch('messages', queryset=Message.objects.select_related('sender')),
        )
        .distinct()
    )


def _get_membership(conversation_id, user):
    """Return the user's ConversationMember for a conversation or None."""
    return ConversationMember.objects.filter(
        conversation_id=conversation_id, user=user
    ).select_related('conversation').first()


class ChatUsersView(APIView):
    """List tenant users available to start a conversation with."""

    permission_classes = [IsAuthenticated]

    @extend_schema(tags=['app-chat'], summary='List tenant users for chat')
    def get(self, request):
        users = User.objects.filter(
            tenant=request.tenant, is_active=True
        ).exclude(id=request.user.id).order_by('name')
        return Response({'users': ChatUserSerializer(users, many=True).data})


class ConversationListCreateView(AuditMixin, APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=['app-chat'], summary='List my conversations')
    def get(self, request):
        qs = _member_qs(request.user)
        data = ConversationListSerializer(
            qs, many=True, context={'request': request}
        ).data
        return Response({'results': data, 'count': len(data)})

    @extend_schema(tags=['app-chat'], summary='Create a direct or group conversation')
    def post(self, request):
        conv_type = request.data.get('type', 'direct')
        member_ids = request.data.get('member_ids', [])
        if not isinstance(member_ids, list) or not member_ids:
            raise ValidationError({'member_ids': 'Se requiere al menos un miembro.'})

        # Eligible: users of the same tenant OR accepted cross-tenant connections.
        eligible_ids = set(
            User.objects.filter(
                tenant=request.tenant, is_active=True
            ).exclude(id=request.user.id).values_list('id', flat=True)
        ) | connected_user_ids(request.user)
        others = list(
            User.objects.filter(id__in=member_ids, is_active=True).exclude(id=request.user.id)
        )
        if not others or any(u.id not in eligible_ids for u in others):
            raise ValidationError(
                {'member_ids': 'Miembros inválidos: solo usuarios de tu cuenta o conexiones aceptadas.'}
            )

        if conv_type == 'direct':
            return self._create_direct(request, others)
        return self._create_group(request, others)

    def _create_direct(self, request, others):
        if len(others) != 1:
            raise ValidationError({'member_ids': 'Un chat directo requiere exactamente un miembro.'})
        other = others[0]
        # get-or-create: reuse existing direct thread between the two users.
        # Resolve candidates first (multi-join filter), then count members in a
        # separate query so the join filter does not inflate the aggregate.
        candidate_ids = (
            Conversation.objects.filter(type='direct', members__user=request.user)
            .filter(members__user=other)
            .values_list('id', flat=True)
        )
        existing = (
            Conversation.objects.filter(id__in=list(candidate_ids))
            .annotate(n=Count('members'))
            .filter(n=2)
            .first()
        )
        if existing:
            data = ConversationDetailSerializer(existing, context={'request': request}).data
            return Response(data, status=status.HTTP_200_OK)

        # Cross-tenant direct threads have no single owning tenant.
        is_cross_tenant = other.tenant_id != request.tenant.id
        with transaction.atomic():
            conv = Conversation.objects.create(
                tenant=None if is_cross_tenant else request.tenant,
                type='direct', created_by=request.user,
            )
            ConversationMember.objects.create(conversation=conv, user=request.user, role='owner')
            ConversationMember.objects.create(conversation=conv, user=other, role='member')
        self.log_action(
            request, 'chat.conversation.create', 'conversation', conv.id,
            {'type': 'direct', 'cross_tenant': is_cross_tenant},
        )
        broadcast_membership_changed(conv.id, [request.user.id, other.id])
        data = ConversationDetailSerializer(conv, context={'request': request}).data
        return Response(data, status=status.HTTP_201_CREATED)

    def _create_group(self, request, others):
        name = (request.data.get('name') or '').strip()
        if not name:
            raise ValidationError({'name': 'El nombre del grupo es obligatorio.'})
        with transaction.atomic():
            conv = Conversation.objects.create(
                tenant=request.tenant, type='group', name=name, created_by=request.user
            )
            ConversationMember.objects.create(conversation=conv, user=request.user, role='owner')
            ConversationMember.objects.bulk_create([
                ConversationMember(conversation=conv, user=u, role='member') for u in others
            ])
        self.log_action(request, 'chat.conversation.create', 'conversation', conv.id, {'type': 'group'})
        broadcast_membership_changed(conv.id, [request.user.id] + [u.id for u in others])
        data = ConversationDetailSerializer(conv, context={'request': request}).data
        return Response(data, status=status.HTTP_201_CREATED)


class SelfConversationView(AuditMixin, APIView):
    """Get-or-create the user's personal "Mensajes guardados" thread.

    A self-chat is a ``type='self'`` conversation with a single member (the
    user). Idempotent: repeated calls return the same thread.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(tags=['app-chat'], summary='Get or create my saved-messages chat')
    def post(self, request):
        existing = (
            Conversation.objects.filter(
                type='self', tenant=request.tenant, members__user=request.user
            )
            .order_by('created_at')
            .first()
        )
        if existing:
            conv = _member_qs(request.user).get(pk=existing.pk)
            return Response(
                ConversationDetailSerializer(conv, context={'request': request}).data,
                status=status.HTTP_200_OK,
            )

        with transaction.atomic():
            conv = Conversation.objects.create(
                tenant=request.tenant, type='self', created_by=request.user,
            )
            ConversationMember.objects.create(
                conversation=conv, user=request.user, role='owner'
            )
        self.log_action(request, 'chat.conversation.create', 'conversation', conv.id, {'type': 'self'})
        conv = _member_qs(request.user).get(pk=conv.pk)
        return Response(
            ConversationDetailSerializer(conv, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )


class ConversationDetailView(AuditMixin, APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=['app-chat'], summary='Get conversation detail')
    def get(self, request, pk):
        membership = _get_membership(pk, request.user)
        if not membership:
            return _NOT_FOUND
        conv = _member_qs(request.user).get(pk=pk)
        return Response(ConversationDetailSerializer(conv, context={'request': request}).data)

    @extend_schema(tags=['app-chat'], summary='Rename a group conversation')
    def patch(self, request, pk):
        membership = _get_membership(pk, request.user)
        if not membership:
            return _NOT_FOUND
        if membership.role not in ('owner', 'admin'):
            raise PermissionDenied('Solo el propietario o un administrador puede renombrar el grupo.')
        conv = membership.conversation
        if conv.type != 'group':
            raise ValidationError({'type': 'Solo los grupos se pueden renombrar.'})
        name = (request.data.get('name') or '').strip()
        if not name:
            raise ValidationError({'name': 'El nombre no puede estar vacío.'})
        conv.name = name
        conv.save(update_fields=['name', 'updated_at'])
        conv = _member_qs(request.user).get(pk=pk)
        return Response(ConversationDetailSerializer(conv, context={'request': request}).data)

    @extend_schema(tags=['app-chat'], summary='Leave a conversation')
    def delete(self, request, pk):
        membership = _get_membership(pk, request.user)
        if not membership:
            return _NOT_FOUND
        membership.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MarkReadView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=['app-chat'], summary='Mark a conversation as read')
    def post(self, request, pk):
        membership = _get_membership(pk, request.user)
        if not membership:
            return _NOT_FOUND
        membership.last_read_at = timezone.now()
        membership.save(update_fields=['last_read_at', 'updated_at'])
        return Response({'status': 'ok', 'last_read_at': membership.last_read_at})


class GroupMemberView(AuditMixin, APIView):
    permission_classes = [IsAuthenticated]

    def _require_admin(self, pk, request):
        membership = _get_membership(pk, request.user)
        if not membership:
            return None, _NOT_FOUND
        if membership.role not in ('owner', 'admin'):
            raise PermissionDenied('Solo el propietario o un administrador puede gestionar miembros.')
        if membership.conversation.type != 'group':
            raise ValidationError({'type': 'Solo los grupos admiten gestión de miembros.'})
        return membership, None

    @extend_schema(tags=['app-chat'], summary='Add a member to a group')
    def post(self, request, pk):
        membership, error = self._require_admin(pk, request)
        if error:
            return error
        user_id = request.data.get('user_id')
        new_user = User.objects.filter(id=user_id, is_active=True).first()
        # Eligible: same tenant OR accepted connection of the actor.
        eligible = new_user and (
            new_user.tenant_id == request.tenant.id
            or new_user.id in connected_user_ids(request.user)
        )
        if not eligible:
            raise ValidationError(
                {'user_id': 'Usuario inválido: solo de tu cuenta o conexiones aceptadas.'}
            )
        member, created = ConversationMember.objects.get_or_create(
            conversation=membership.conversation, user=new_user,
            defaults={'role': 'member'},
        )
        if created:
            broadcast_membership_changed(membership.conversation_id, [new_user.id])
        conv = _member_qs(request.user).get(pk=pk)
        return Response(
            ConversationDetailSerializer(conv, context={'request': request}).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @extend_schema(
        tags=['app-chat'], summary='Remove a member from a group',
        parameters=[OpenApiParameter('user_id', OpenApiTypes.UUID, description='Member to remove')],
    )
    def delete(self, request, pk):
        membership, error = self._require_admin(pk, request)
        if error:
            return error
        user_id = request.query_params.get('user_id') or request.data.get('user_id')
        ConversationMember.objects.filter(
            conversation=membership.conversation, user_id=user_id
        ).exclude(role='owner').delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MessageListCreateView(AuditMixin, APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=['app-chat'], summary='List messages of a conversation',
        parameters=[
            OpenApiParameter('conversation', OpenApiTypes.UUID, required=True),
            OpenApiParameter('before', OpenApiTypes.DATETIME, description='Cursor: load older than this'),
        ],
    )
    def get(self, request):
        conversation_id = request.query_params.get('conversation')
        if not conversation_id:
            raise ValidationError({'conversation': 'Parámetro requerido.'})
        membership = _get_membership(conversation_id, request.user)
        if not membership:
            return _NOT_FOUND
        qs = Message.objects.filter(conversation_id=conversation_id).select_related(
            'sender', 'reply_to', 'reply_to__sender'
        ).prefetch_related('attachments')
        before = request.query_params.get('before')
        if before:
            qs = qs.filter(created_at__lt=before)
        # Newest page first from DB, then return chronological for the UI.
        page = list(qs.order_by('-created_at')[:_MESSAGES_PAGE_SIZE])
        page.reverse()
        has_more = qs.count() > _MESSAGES_PAGE_SIZE
        data = MessageSerializer(page, many=True, context={'request': request}).data
        return Response({'results': data, 'count': len(data), 'has_more': has_more})

    @extend_schema(tags=['app-chat'], summary='Send a message (text and/or attachment)')
    def post(self, request):
        conversation_id = request.data.get('conversation')
        membership = _get_membership(conversation_id, request.user)
        if not membership:
            return _NOT_FOUND
        content = (request.data.get('content') or '').strip()
        upload = request.FILES.get('file')
        if not content and not upload:
            raise ValidationError({'content': 'El mensaje no puede estar vacío.'})
        if upload and upload.size > _MAX_ATTACHMENT_BYTES:
            raise ValidationError({'file': 'El archivo supera el límite de 10 MB.'})

        reply_to = None
        reply_to_id = request.data.get('reply_to')
        if reply_to_id:
            reply_to = Message.objects.filter(
                id=reply_to_id, conversation_id=conversation_id
            ).first()
            if not reply_to:
                raise ValidationError({'reply_to': 'El mensaje citado no pertenece a esta conversación.'})

        with transaction.atomic():
            message = Message.objects.create(
                conversation=membership.conversation,
                sender=request.user,
                content=content,
                reply_to=reply_to,
            )
            if upload:
                kind = 'image' if (upload.content_type or '').startswith('image/') else 'file'
                MessageAttachment.objects.create(
                    message=message, file=upload, kind=kind,
                    original_name=upload.name[:255], size=upload.size,
                )
            # Bump conversation ordering + mark sender as caught up.
            membership.conversation.save(update_fields=['updated_at'])
            membership.last_read_at = timezone.now()
            membership.save(update_fields=['last_read_at', 'updated_at'])
        message_data = MessageSerializer(message, context={'request': request}).data
        broadcast_message(membership.conversation_id, message_data)
        return Response(message_data, status=status.HTTP_201_CREATED)


class MessageConvertView(AuditMixin, APIView):
    """Convert a chat message into a Note, Contact or CodeSnippet (own tenant)."""

    permission_classes = [IsAuthenticated]

    _TARGETS = ('note', 'contact', 'snippet')

    @extend_schema(tags=['app-chat'], summary='Convert a message to note/contact/snippet')
    def post(self, request, pk):
        message = Message.objects.filter(id=pk).select_related('sender').first()
        if not message:
            return _NOT_FOUND
        if not _get_membership(message.conversation_id, request.user):
            return _NOT_FOUND
        target = request.data.get('target')
        if target not in self._TARGETS:
            raise ValidationError({'target': f'Debe ser uno de: {", ".join(self._TARGETS)}.'})

        if target == 'note':
            obj, payload = self._to_note(request, message)
        elif target == 'contact':
            obj, payload = self._to_contact(request, message)
        else:
            obj, payload = self._to_snippet(request, message)

        self.log_action(request, 'chat.message.convert', target, obj.id, {'message_id': str(message.id)})
        return Response(
            {'target': target, 'id': str(obj.id), **payload},
            status=status.HTTP_201_CREATED,
        )

    def _require(self, request, permission):
        if not _user_has_permission(request.user, permission):
            raise PermissionDenied()

    def _to_note(self, request, message):
        from apps.notes.models import Note
        self._require(request, 'notes.create')
        count = Note.objects.filter(tenant=request.tenant, user=request.user).count()
        check_plan_limit(request.user, 'notes', count)
        title = (request.data.get('title') or message.content[:50] or 'Nota').strip()
        note = Note.objects.create(
            tenant=request.tenant, user=request.user,
            title=title, content=message.content,
        )
        return note, {'title': note.title}

    def _to_contact(self, request, message):
        from apps.contacts.models import Contact
        self._require(request, 'contacts.create')
        count = Contact.objects.filter(tenant=request.tenant, user=request.user).count()
        check_plan_limit(request.user, 'contacts', count)
        parts = message.sender.name.split(' ', 1)
        contact = Contact.objects.create(
            tenant=request.tenant, user=request.user,
            first_name=parts[0] or 'Contacto',
            last_name=parts[1] if len(parts) > 1 else '',
            email=getattr(message.sender, 'email', '') or '',
            notes=message.content,
        )
        return contact, {'name': f'{contact.first_name} {contact.last_name}'.strip()}

    def _to_snippet(self, request, message):
        from apps.snippets.models import CodeSnippet
        self._require(request, 'snippets.create')
        count = CodeSnippet.objects.filter(tenant=request.tenant, user=request.user).count()
        check_plan_limit(request.user, 'snippets', count)
        title = (request.data.get('title') or message.content[:50] or 'Snippet').strip()
        language = request.data.get('language', 'other')
        snippet = CodeSnippet.objects.create(
            tenant=request.tenant, user=request.user,
            title=title, code=message.content, language=language,
        )
        return snippet, {'title': snippet.title}


class ConnectionListCreateView(AuditMixin, APIView):
    """List my chat connections and invite a registered user by email."""

    permission_classes = [IsAuthenticated]

    @extend_schema(tags=['app-chat'], summary='List my chat connections')
    def get(self, request):
        qs = ChatConnection.objects.filter(
            Q(requester=request.user) | Q(addressee=request.user)
        ).select_related('requester', 'addressee', 'requester_tenant', 'addressee_tenant')
        ctx = {'request': request}
        accepted, incoming, outgoing = [], [], []
        for conn in qs:
            data = ChatConnectionSerializer(conn, context=ctx).data
            if conn.status == 'accepted':
                accepted.append(data)
            elif conn.status == 'pending':
                (incoming if conn.addressee_id == request.user.id else outgoing).append(data)
        return Response({
            'accepted': accepted,
            'pending_incoming': incoming,
            'pending_outgoing': outgoing,
        })

    @extend_schema(tags=['app-chat'], summary='Invite a user by email (registered or not)')
    def post(self, request):
        email = (request.data.get('email') or '').strip().lower()
        if not email:
            raise ValidationError({'email': 'El email es obligatorio.'})
        target = User.objects.filter(email__iexact=email, is_active=True).first()
        if not target:
            return self._invite_unregistered(request, email)
        if target.id == request.user.id:
            raise ValidationError({'email': 'No puedes conectarte contigo mismo.'})

        existing = ChatConnection.objects.filter(
            Q(requester=request.user, addressee=target)
            | Q(requester=target, addressee=request.user)
        ).first()
        if existing:
            return Response(
                ChatConnectionSerializer(existing, context={'request': request}).data,
                status=status.HTTP_200_OK,
            )

        conn = ChatConnection.objects.create(
            requester=request.user,
            addressee=target,
            requester_tenant=request.tenant,
            addressee_tenant=target.tenant,
            status='pending',
        )
        self.log_action(request, 'chat.connection.invite', 'connection', conn.id, {'addressee': str(target.id)})
        self._notify(request, target)
        return Response(
            ChatConnectionSerializer(conn, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )

    def _invite_unregistered(self, request, email):
        """Email is not a registered user → create a pending email invite.

        The connection is linked to the new user automatically on registration
        (apps/chat/signals.py), then it can be accepted normally.
        """
        existing = ChatConnection.objects.filter(
            requester=request.user, invited_email__iexact=email, addressee__isnull=True
        ).first()
        if existing:
            return Response(
                ChatConnectionSerializer(existing, context={'request': request}).data,
                status=status.HTTP_200_OK,
            )
        conn = ChatConnection.objects.create(
            requester=request.user,
            addressee=None,
            invited_email=email,
            requester_tenant=request.tenant,
            status='pending',
        )
        self.log_action(request, 'chat.connection.invite_email', 'connection', conn.id, {'email': email})
        self._notify_register(request, email)
        return Response(
            ChatConnectionSerializer(conn, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )

    def _notify(self, request, target):
        company = request.tenant.name if request.tenant else ''
        self._send(
            target.email,
            'Nueva solicitud de conexión en el Chat',
            f'{request.user.name} de {company} quiere conectar contigo en el Chat. '
            f'Entra a tu Workspace → Chat → Conexiones para aceptar la solicitud.',
        )

    def _notify_register(self, request, email):
        company = request.tenant.name if request.tenant else ''
        register_url = f'{getattr(settings, "FRONTEND_HUB_URL", settings.FRONTEND_URL)}/register'
        self._send(
            email,
            'Te invitaron al Chat',
            f'{request.user.name} de {company} quiere conectar contigo en el Chat. '
            f'Crea tu cuenta para aceptar la invitación: {register_url}',
        )

    @staticmethod
    def _send(recipient, subject, message):
        try:
            send_mail(
                subject=subject,
                message=message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[recipient],
                fail_silently=True,
            )
        except Exception:
            pass  # Notification failure must not block the connection request


class ConnectionRespondView(AuditMixin, APIView):
    """Accept, reject or block a pending connection (addressee only)."""

    permission_classes = [IsAuthenticated]

    _ACTIONS = {'accept': 'accepted', 'reject': 'rejected', 'block': 'blocked'}

    @extend_schema(tags=['app-chat'], summary='Respond to a connection request')
    def post(self, request, pk):
        conn = ChatConnection.objects.filter(id=pk).first()
        if not conn or conn.addressee_id != request.user.id:
            return _NOT_FOUND
        action = request.data.get('action')
        if action not in self._ACTIONS:
            raise ValidationError({'action': 'Debe ser accept, reject o block.'})

        if action == 'reject':
            conn.delete()
            self.log_action(request, 'chat.connection.reject', 'connection', pk, {})
            return Response(status=status.HTTP_204_NO_CONTENT)

        conn.status = self._ACTIONS[action]
        conn.responded_at = timezone.now()
        conn.save(update_fields=['status', 'responded_at', 'updated_at'])
        self.log_action(request, f'chat.connection.{action}', 'connection', pk, {})
        return Response(ChatConnectionSerializer(conn, context={'request': request}).data)
