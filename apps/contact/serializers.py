from rest_framework import serializers

from apps.contact.models import ContactMessage


class ContactMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ContactMessage
        fields = [
            'id', 'name', 'email', 'phone', 'message',
            'status', 'ip_address', 'created_at', 'updated_at',
        ]


class ContactMessageCreateSerializer(serializers.Serializer):
    name            = serializers.CharField(max_length=255)
    email           = serializers.EmailField()
    phone           = serializers.CharField(max_length=30, required=False, allow_blank=True)
    message         = serializers.CharField(min_length=10)
    recaptcha_token = serializers.CharField()


class ContactMessageUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=ContactMessage.STATUS_CHOICES)
