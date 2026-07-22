from rest_framework import serializers

from utils.media import build_media_url
from utils.uploads import validate_upload

from .models import CatalogItem


class CatalogItemSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = CatalogItem
        fields = [
            'id',
            'name',
            'short_description',
            'description',
            'image_url',
            'icon_color',
            'category',
            'link_url',
            'badge_text',
            'target_apps',
            'is_active',
            'order',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at', 'image_url']

    def get_image_url(self, obj: CatalogItem) -> str | None:
        return build_media_url(obj.image, self.context.get('request'))

    def to_internal_value(self, data):
        # Accept 'image' as file upload alongside other fields
        return super().to_internal_value(data)

    def update(self, instance: CatalogItem, validated_data: dict) -> CatalogItem:
        new_image = validated_data.get('image')
        if new_image and instance.image:
            instance.image.delete(save=False)
        return super().update(instance, validated_data)


class CatalogItemWriteSerializer(CatalogItemSerializer):
    image = serializers.ImageField(required=False, allow_null=True)

    class Meta(CatalogItemSerializer.Meta):
        fields = CatalogItemSerializer.Meta.fields + ['image']

    def validate_image(self, value):
        if value:
            validate_upload(value, category='platform_image')
        return value
