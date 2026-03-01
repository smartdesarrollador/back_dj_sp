# Generated manually for Paso 15 — MFA Recovery Codes

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('auth_app', '0002_rename_users_email_idx_users_email_4b85f2_idx_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='MFARecoveryCode',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('code_hash', models.CharField(max_length=128)),
                ('is_used', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='mfa_recovery_codes',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'db_table': 'mfa_recovery_codes',
            },
        ),
    ]
