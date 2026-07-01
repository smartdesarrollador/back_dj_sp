from rest_framework import serializers

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
        if not obj.image:
            return None
        request = self.context.get('request')
        if request:
            return request.build_absolute_uri(obj.image.url)
        return obj.image.url

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
        if value and value.size > 2 * 1024 * 1024:
            raise serializers.ValidationError('La imagen no puede superar 2 MB.')
        return value
