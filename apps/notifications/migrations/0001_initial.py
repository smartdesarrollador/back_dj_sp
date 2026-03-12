import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('tenants', '0001_initial_tenant_model'),
    ]

    operations = [
        migrations.CreateModel(
            name='Notification',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('category', models.CharField(
                    choices=[
                        ('security', 'Seguridad'),
                        ('billing', 'Facturación'),
                        ('system', 'Sistema'),
                        ('users', 'Usuarios'),
                        ('roles', 'Roles'),
                        ('services', 'Servicios'),
                    ],
                    max_length=20,
                )),
                ('title', models.CharField(max_length=200)),
                ('message', models.TextField(blank=True)),
                ('icon', models.CharField(blank=True, max_length=50)),
                ('read', models.BooleanField(default=False)),
                ('tenant', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='notifications',
                    to='tenants.tenant',
                )),
            ],
            options={
                'db_table': 'notifications',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='notification',
            index=models.Index(fields=['tenant', 'read'], name='notif_tenant_read_idx'),
        ),
        migrations.AddIndex(
            model_name='notification',
            index=models.Index(fields=['tenant', 'category'], name='notif_tenant_category_idx'),
        ),
    ]
