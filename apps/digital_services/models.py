"""
Digital Services models — public profile pages.

Models:
  PublicProfile    — user's public identity (username, bio, SEO meta)
  DigitalCard      — contact info + social links + QR
  LandingTemplate  — landing page builder
  PortfolioItem    — portfolio case study
  CVDocument       — curriculum vitae content
  CustomDomain     — enterprise custom domain mapping
"""
from django.conf import settings
from django.db import models

from core.models import BaseModel


class PublicProfile(BaseModel):
    """
    Root profile tying all digital service pages to a user.
    username is the public slug used in all public URLs.
    """
    RESERVED_USERNAMES = frozenset([
        'admin', 'api', 'www', 'app', 'dashboard', 'login', 'register',
        'help', 'support', 'public', 'landing', 'cv', 'portafolio',
    ])

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='public_profile',
    )
    username = models.SlugField(unique=True, max_length=50, db_index=True)
    display_name = models.CharField(max_length=100)
    title = models.CharField(max_length=100, blank=True)
    bio = models.TextField(max_length=500, blank=True)
    avatar_url = models.URLField(blank=True)
    is_public = models.BooleanField(default=False)
    meta_title = models.CharField(max_length=60, blank=True)
    meta_description = models.CharField(max_length=160, blank=True)
    og_image_url = models.URLField(blank=True)

    class Meta:
        db_table = 'public_profiles'
        indexes = [
            models.Index(fields=['username'], name='public_profiles_username_idx'),
            models.Index(fields=['is_public', 'created_at'], name='pub_profiles_pub_created_idx'),
        ]

    def __str__(self) -> str:
        return f'@{self.username}'


class DigitalCard(BaseModel):
    """
    Contact card linked 1-to-1 with a PublicProfile.
    Holds social links, contact info, and QR code data URL.
    """
    profile = models.OneToOneField(
        PublicProfile,
        on_delete=models.CASCADE,
        related_name='digital_card',
    )
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    location = models.CharField(max_length=100, blank=True)
    linkedin_url = models.URLField(blank=True)
    twitter_url = models.URLField(blank=True)
    github_url = models.URLField(blank=True)
    instagram_url = models.URLField(blank=True)
    facebook_url = models.URLField(blank=True)
    website_url = models.URLField(blank=True)
    primary_color = models.CharField(max_length=7, default='#3B82F6')
    background_color = models.CharField(max_length=7, default='#FFFFFF')
    # Base64 data URL or external URL for the QR code image
    qr_code_url = models.TextField(blank=True)

    class Meta:
        db_table = 'digital_cards'

    def __str__(self) -> str:
        return f'Card for @{self.profile.username}'


class LandingTemplate(BaseModel):
    """
    Landing page builder for a user's public profile.
    sections is a JSON list of section blocks: [{type, props}, ...]
    """
    TEMPLATE_CHOICES = [
        ('basic', 'Basic'),
        ('minimal', 'Minimal'),
        ('corporate', 'Corporate'),
        ('creative', 'Creative'),
    ]

    profile = models.OneToOneField(
        PublicProfile,
        on_delete=models.CASCADE,
        related_name='landing',
    )
    template_type = models.CharField(
        max_length=20,
        choices=TEMPLATE_CHOICES,
        default='basic',
    )
    sections = models.JSONField(default=list)
    contact_email = models.EmailField(blank=True)
    enable_contact_form = models.BooleanField(default=False)
    custom_css = models.TextField(blank=True)
    ga_tracking_id = models.CharField(max_length=20, blank=True)

    class Meta:
        db_table = 'landing_templates'

    def __str__(self) -> str:
        return f'Landing for @{self.profile.username}'


class PortfolioItem(BaseModel):
    """
    A single portfolio case study.  Multiple per profile (ForeignKey).
    slug must be unique per profile.
    """
    profile = models.ForeignKey(
        PublicProfile,
        on_delete=models.CASCADE,
        related_name='portfolio_items',
    )
    title = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100)
    description_short = models.CharField(max_length=200)
    description_full = models.TextField(blank=True)
    cover_image_url = models.URLField()
    gallery_images = models.JSONField(default=list)
    demo_url = models.URLField(blank=True)
    repo_url = models.URLField(blank=True)
    case_study_url = models.URLField(blank=True)
    tags = models.JSONField(default=list)
    is_featured = models.BooleanField(default=False)
    order = models.IntegerField(default=0)
    project_date = models.DateField()

    class Meta:
        db_table = 'portfolio_items'
        ordering = ['-is_featured', 'order', '-project_date']
        unique_together = [['profile', 'slug']]
        indexes = [
            models.Index(fields=['profile', 'is_featured'], name='portf_items_prof_feat_idx'),
        ]

    def __str__(self) -> str:
        return self.title


class CVDocument(BaseModel):
    """
    Curriculum Vitae content for a public profile.
    All sections (experience, education, skills…) stored as JSON arrays.
    """
    profile = models.OneToOneField(
        PublicProfile,
        on_delete=models.CASCADE,
        related_name='cv',
    )
    professional_summary = models.TextField(max_length=500, blank=True)
    # [{company, position, start, end, description}, ...]
    experience = models.JSONField(default=list)
    # [{institution, degree, field, start, end}, ...]
    education = models.JSONField(default=list)
    # ['Python', 'React', ...]
    skills = models.JSONField(default=list)
    # [{language, level}, ...]
    languages = models.JSONField(default=list)
    # [{title, issuer, date, url}, ...]
    certifications = models.JSONField(default=list)
    template_type = models.CharField(max_length=20, default='classic')
    show_photo = models.BooleanField(default=True)
    show_contact = models.BooleanField(default=True)

    class Meta:
        db_table = 'cv_documents'

    def __str__(self) -> str:
        return f'CV for @{self.profile.username}'


class CustomDomain(BaseModel):
    """
    Enterprise-only custom domain mapping for a public profile.
    Includes DNS verification flow and SSL status tracking.
    """
    VERIFICATION_CHOICES = [
        ('pending', 'Pending'),
        ('verified', 'Verified'),
        ('failed', 'Failed'),
    ]

    profile = models.OneToOneField(
        PublicProfile,
        on_delete=models.CASCADE,
        related_name='custom_domain',
    )
    domain = models.CharField(max_length=255, unique=True)
    verification_status = models.CharField(
        max_length=20,
        choices=VERIFICATION_CHOICES,
        default='pending',
    )
    verification_token = models.CharField(max_length=64, unique=True)
    last_verification_attempt = models.DateTimeField(null=True, blank=True)
    ssl_status = models.CharField(max_length=20, default='pending')
    ssl_cert_expires_at = models.DateTimeField(null=True, blank=True)
    default_service = models.CharField(max_length=20, default='landing')

    class Meta:
        db_table = 'custom_domains'

    def __str__(self) -> str:
        return self.domain
