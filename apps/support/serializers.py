"""
Support serializers — read/write for tickets and comments.
"""
from rest_framework import serializers

from apps.support.models import SupportTicket, TicketComment


class TicketCommentSerializer(serializers.ModelSerializer):
    class Meta:
        model = TicketComment
        fields = ['id', 'author', 'role', 'message', 'created_at']
        read_only_fields = ['id', 'created_at']


class SupportTicketSerializer(serializers.ModelSerializer):
    comments = TicketCommentSerializer(many=True, read_only=True)

    class Meta:
        model = SupportTicket
        fields = [
            'id', 'reference', 'subject', 'description', 'category',
            'priority', 'status', 'client_email', 'resolved_at',
            'client', 'assigned_to', 'comments', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'reference', 'resolved_at', 'created_at', 'updated_at']


class TicketCreateSerializer(serializers.Serializer):
    subject = serializers.CharField(max_length=255)
    description = serializers.CharField()
    category = serializers.ChoiceField(choices=SupportTicket.CATEGORY_CHOICES)
    priority = serializers.ChoiceField(
        choices=SupportTicket.PRIORITY_CHOICES, default='media'
    )


class TicketUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=SupportTicket.STATUS_CHOICES, required=False)
    priority = serializers.ChoiceField(choices=SupportTicket.PRIORITY_CHOICES, required=False)
    assigned_to = serializers.UUIDField(required=False, allow_null=True)


class TicketCommentCreateSerializer(serializers.Serializer):
    message = serializers.CharField()
    role = serializers.ChoiceField(
        choices=TicketComment.ROLE_CHOICES, default='client'
    )
