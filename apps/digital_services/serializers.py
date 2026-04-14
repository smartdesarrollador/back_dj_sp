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
            'id', 'template_type', 'sections', 'contact_email',
            'enable_contact_form', 'custom_css', 'ga_tracking_id',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class PortfolioItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = PortfolioItem
        fields = [
            'id', 'title', 'slug', 'description_short', 'description_full',
            'cover_image_url', 'gallery_images', 'demo_url', 'repo_url',
            'case_study_url', 'tags', 'is_featured', 'order', 'project_date',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate(self, attrs):
        # Auto-generate slug from title if not provided
        if not attrs.get('slug') and attrs.get('title'):
            from django.utils.text import slugify
            attrs['slug'] = slugify(attrs['title'])[:100]
        return attrs


class CVDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = CVDocument
        fields = [
            'id', 'professional_summary', 'experience', 'education',
            'skills', 'languages', 'certifications',
            'template_type', 'show_photo', 'show_contact',
            'created_at', 'updated_at',
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
