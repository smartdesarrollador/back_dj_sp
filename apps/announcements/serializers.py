from rest_framework import serializers

from utils.media import build_media_url
from utils.uploads import validate_upload

from .models import Announcement


class AnnouncementSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = Announcement
        fields = [
            'id',
            'title',
            'message',
            'image_url',
            'cta_text',
            'cta_url',
            'placement',
            'is_active',
            'starts_at',
            'ends_at',
            'priority',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at', 'image_url']

    def get_image_url(self, obj: Announcement) -> str | None:
        return build_media_url(obj.image, self.context.get('request'))


class AnnouncementWriteSerializer(AnnouncementSerializer):
    image = serializers.ImageField(required=False, allow_null=True)

    class Meta(AnnouncementSerializer.Meta):
        fields = AnnouncementSerializer.Meta.fields + ['image']

    def validate_image(self, value):
        if value:
            validate_upload(value, category='platform_image')
        return value

    def update(self, instance: Announcement, validated_data: dict) -> Announcement:
        new_image = validated_data.get('image')
        if new_image and instance.image:
            instance.image.delete(save=False)
        return super().update(instance, validated_data)
