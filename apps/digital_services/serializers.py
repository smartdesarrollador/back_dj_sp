"""
Serializers for the Digital Services module.
"""
import re

from rest_framework import serializers

from apps.digital_services.models import (
    CVDocument,
    CustomDomain,
    DigitalCard,
    LandingTemplate,
    PortfolioItem,
    PortfolioSettings,
    PublicProfile,
)

_USERNAME_RE = re.compile(r'^[a-z0-9]([a-z0-9\-]{0,48}[a-z0-9])?$')


class PublicProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = PublicProfile
        fields = [
            'id', 'username', 'display_name', 'title', 'bio',
            'avatar_url', 'is_public', 'meta_title', 'meta_description',
            'og_image_url', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_username(self, value: str) -> str:
        value = value.lower()
        if value in PublicProfile.RESERVED_USERNAMES:
            raise serializers.ValidationError(
                f"'{value}' is a reserved username."
            )
        if not _USERNAME_RE.match(value):
            raise serializers.ValidationError(
                'Username must be 1–50 lowercase alphanumeric characters or hyphens, '
                'starting and ending with a letter or digit.'
            )
        return value


class DigitalCardSerializer(serializers.ModelSerializer):
    class Meta:
        model = DigitalCard
        fields = [
            'id', 'email', 'phone', 'location',
            'linkedin_url', 'twitter_url', 'github_url',
            'instagram_url', 'facebook_url', 'website_url',
            'primary_color', 'background_color', 'qr_code_url',
            'specialties', 'years_experience',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'qr_code_url', 'created_at', 'updated_at']


class LandingTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = LandingTemplate
        fields = [
            'id', 'template_type', 'style_preset', 'sections', 'contact_email',
            'enable_contact_form', 'custom_css', 'ga_tracking_id',
            'social_links', 'accent_color', 'theme_colors',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class PortfolioItemSerializer(serializers.ModelSerializer):
    slug = serializers.SlugField(max_length=100, required=False, allow_blank=True)
    cover_image_url = serializers.URLField(required=False, allow_blank=True)

    class Meta:
        model = PortfolioItem
        fields = [
            'id', 'title', 'slug', 'description_short', 'description_full',
            'cover_image_url', 'gallery_images', 'demo_url', 'repo_url',
            'case_study_url', 'tags', 'is_featured', 'is_published', 'order', 'project_date',
            'category', 'client_name', 'technologies', 'duration', 'status', 'accent_color',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate(self, attrs):
        from django.utils.text import slugify
        title = attrs.get('title') or (self.instance.title if self.instance else '')
        if not attrs.get('slug') and title:
            base_slug = slugify(title)[:90]
            profile = attrs.get('profile') or (self.instance.profile if self.instance else None)
            slug = base_slug
            n = 1
            while profile and PortfolioItem.objects.filter(profile=profile, slug=slug).exclude(
                pk=self.instance.pk if self.instance else None
            ).exists():
                slug = f'{base_slug}-{n}'
                n += 1
            attrs['slug'] = slug
        return attrs


class CVDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = CVDocument
        fields = [
            'id', 'professional_summary', 'experience', 'education',
            'skills', 'languages', 'certifications',
            'template_type', 'show_photo', 'show_contact',
            'headline', 'location', 'website_url', 'linkedin_url', 'github_url',
            'accent_color', 'theme_colors', 'style_preset', 'is_published', 'projects',
            'volunteer', 'awards', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class CustomDomainSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomDomain
        fields = [
            'id', 'domain', 'verification_status', 'verification_token',
            'last_verification_attempt', 'ssl_status', 'ssl_cert_expires_at',
            'default_service', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'verification_status', 'verification_token',
            'last_verification_attempt', 'ssl_status', 'ssl_cert_expires_at',
            'created_at', 'updated_at',
        ]


class PortfolioSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = PortfolioSettings
        fields = [
            'id', 'style_preset', 'theme_colors', 'hero_content', 'contact_content', 'about_content',
            'skills_content', 'services_content', 'testimonials_content', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
