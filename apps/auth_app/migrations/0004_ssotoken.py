import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('auth_app', '0003_mfarecoverycode'),
        ('tenants', '0002_rename_tenants_slug_idx_tenants_slug_3181c2_idx_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='SSOToken',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('service', models.CharField(max_length=50)),
                ('token', models.CharField(db_index=True, max_length=64, unique=True)),
                ('used_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('expires_at', models.DateTimeField(db_index=True)),
                ('tenant', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='sso_tokens',
                    to='tenants.tenant',
                )),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='sso_tokens',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={'db_table': 'sso_tokens'},
        ),
        migrations.AddIndex(
            model_name='ssotoken',
            index=models.Index(fields=['token'], name='sso_tokens_token_idx'),
        ),
        migrations.AddIndex(
            model_name='ssotoken',
            index=models.Index(fields=['expires_at', 'used_at'], name='sso_tokens_expires_used_idx'),
        ),
    ]
