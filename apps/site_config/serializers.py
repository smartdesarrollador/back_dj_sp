from rest_framework import serializers

from apps.site_config.models import FooterConfig, FooterLink


class FooterLinkSerializer(serializers.ModelSerializer):
    class Meta:
        model = FooterLink
        fields = ['id', 'label', 'url', 'order']


class FooterConfigSerializer(serializers.ModelSerializer):
    links = FooterLinkSerializer(many=True, read_only=True)

    class Meta:
        model = FooterConfig
        fields = [
            'tagline', 'email', 'whatsapp', 'phone',
            'facebook_url', 'instagram_url', 'youtube_url', 'linkedin_url',
            'links', 'updated_at',
        ]
        read_only_fields = ['updated_at']


class FooterConfigUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = FooterConfig
        fields = [
            'tagline', 'email', 'whatsapp', 'phone',
            'facebook_url', 'instagram_url', 'youtube_url', 'linkedin_url',
        ]
