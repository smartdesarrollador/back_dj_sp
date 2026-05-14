import django.db.models.deletion
import uuid

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='DesktopAppLicense',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('license_key', models.CharField(max_length=19, unique=True)),
                ('hardware_id', models.CharField(blank=True, max_length=64)),
                ('activated_at', models.DateTimeField(blank=True, null=True)),
                ('expires_at', models.DateTimeField(blank=True, null=True)),
                ('is_active', models.BooleanField(db_index=True, default=True)),
                ('sent_at', models.DateTimeField(blank=True, null=True)),
                ('notes', models.TextField(blank=True)),
                ('user', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='desktop_license',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('created_by', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='created_licenses',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'db_table': 'desktop_app_licenses',
            },
        ),
        migrations.AddIndex(
            model_name='desktopapplicense',
            index=models.Index(fields=['license_key'], name='dal_license_key_idx'),
        ),
        migrations.AddIndex(
            model_name='desktopapplicense',
            index=models.Index(fields=['user', 'is_active'], name='dal_user_active_idx'),
        ),
    ]
