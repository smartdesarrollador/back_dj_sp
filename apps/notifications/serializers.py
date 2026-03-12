from rest_framework import serializers

from apps.notifications.models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ['id', 'category', 'title', 'message', 'icon', 'read', 'created_at']
        read_only_fields = fields
