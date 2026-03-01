# Migration for PASO 18 — Digital Services module

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PublicProfile',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('username', models.SlugField(db_index=True, max_length=50, unique=True)),
                ('display_name', models.CharField(max_length=100)),
                ('title', models.CharField(blank=True, max_length=100)),
                ('bio', models.TextField(blank=True, max_length=500)),
                ('avatar_url', models.URLField(blank=True)),
                ('is_public', models.BooleanField(default=False)),
                ('meta_title', models.CharField(blank=True, max_length=60)),
                ('meta_description', models.CharField(blank=True, max_length=160)),
                ('og_image_url', models.URLField(blank=True)),
                ('user', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='public_profile',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'db_table': 'public_profiles',
                'indexes': [
                    models.Index(fields=['username'], name='public_profiles_username_idx'),
                    models.Index(fields=['is_public', 'created_at'], name='public_profiles_public_created_idx'),
                ],
            },
        ),
        migrations.CreateModel(
            name='DigitalCard',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('email', models.EmailField(blank=True)),
                ('phone', models.CharField(blank=True, max_length=20)),
                ('location', models.CharField(blank=True, max_length=100)),
                ('linkedin_url', models.URLField(blank=True)),
                ('twitter_url', models.URLField(blank=True)),
                ('github_url', models.URLField(blank=True)),
                ('instagram_url', models.URLField(blank=True)),
                ('facebook_url', models.URLField(blank=True)),
                ('website_url', models.URLField(blank=True)),
                ('primary_color', models.CharField(default='#3B82F6', max_length=7)),
                ('background_color', models.CharField(default='#FFFFFF', max_length=7)),
                ('qr_code_url', models.TextField(blank=True)),
                ('profile', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='digital_card',
                    to='digital_services.publicprofile',
                )),
            ],
            options={
                'db_table': 'digital_cards',
            },
        ),
        migrations.CreateModel(
            name='LandingTemplate',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('template_type', models.CharField(
                    choices=[('basic', 'Basic'), ('minimal', 'Minimal'), ('corporate', 'Corporate'), ('creative', 'Creative')],
                    default='basic',
                    max_length=20,
                )),
                ('sections', models.JSONField(default=list)),
                ('contact_email', models.EmailField(blank=True)),
                ('enable_contact_form', models.BooleanField(default=False)),
                ('custom_css', models.TextField(blank=True)),
                ('ga_tracking_id', models.CharField(blank=True, max_length=20)),
                ('profile', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='landing',
                    to='digital_services.publicprofile',
                )),
            ],
            options={
                'db_table': 'landing_templates',
            },
        ),
        migrations.CreateModel(
            name='PortfolioItem',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('title', models.CharField(max_length=100)),
                ('slug', models.SlugField(max_length=100)),
                ('description_short', models.CharField(max_length=200)),
                ('description_full', models.TextField(blank=True)),
                ('cover_image_url', models.URLField()),
                ('gallery_images', models.JSONField(default=list)),
                ('demo_url', models.URLField(blank=True)),
                ('repo_url', models.URLField(blank=True)),
                ('case_study_url', models.URLField(blank=True)),
                ('tags', models.JSONField(default=list)),
                ('is_featured', models.BooleanField(default=False)),
                ('order', models.IntegerField(default=0)),
                ('project_date', models.DateField()),
                ('profile', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='portfolio_items',
                    to='digital_services.publicprofile',
                )),
            ],
            options={
                'db_table': 'portfolio_items',
                'ordering': ['-is_featured', 'order', '-project_date'],
                'unique_together': {('profile', 'slug')},
                'indexes': [
                    models.Index(fields=['profile', 'is_featured'], name='portfolio_items_profile_featured_idx'),
                ],
            },
        ),
        migrations.CreateModel(
            name='CVDocument',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('professional_summary', models.TextField(blank=True, max_length=500)),
                ('experience', models.JSONField(default=list)),
                ('education', models.JSONField(default=list)),
                ('skills', models.JSONField(default=list)),
                ('languages', models.JSONField(default=list)),
                ('certifications', models.JSONField(default=list)),
                ('template_type', models.CharField(default='classic', max_length=20)),
                ('show_photo', models.BooleanField(default=True)),
                ('show_contact', models.BooleanField(default=True)),
                ('profile', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='cv',
                    to='digital_services.publicprofile',
                )),
            ],
            options={
                'db_table': 'cv_documents',
            },
        ),
        migrations.CreateModel(
            name='CustomDomain',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('domain', models.CharField(max_length=255, unique=True)),
                ('verification_status', models.CharField(
                    choices=[('pending', 'Pending'), ('verified', 'Verified'), ('failed', 'Failed')],
                    default='pending',
                    max_length=20,
                )),
                ('verification_token', models.CharField(max_length=64, unique=True)),
                ('last_verification_attempt', models.DateTimeField(blank=True, null=True)),
                ('ssl_status', models.CharField(default='pending', max_length=20)),
                ('ssl_cert_expires_at', models.DateTimeField(blank=True, null=True)),
                ('default_service', models.CharField(default='landing', max_length=20)),
                ('profile', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='custom_domain',
                    to='digital_services.publicprofile',
                )),
            ],
            options={
                'db_table': 'custom_domains',
            },
        ),
    ]
