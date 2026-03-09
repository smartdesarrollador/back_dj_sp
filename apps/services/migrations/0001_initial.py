import uuid
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ('tenants', '0002_rename_tenants_slug_idx_tenants_slug_3181c2_idx_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='Service',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('slug', models.SlugField(unique=True)),
                ('name', models.CharField(max_length=100)),
                ('description', models.TextField(blank=True)),
                ('icon', models.CharField(max_length=50)),
                ('url_template', models.CharField(max_length=255)),
                ('min_plan', models.CharField(default='free', max_length=20)),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={'db_table': 'services'},
        ),
        migrations.CreateModel(
            name='TenantService',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('status', models.CharField(default='active', max_length=20)),
                ('acquired_at', models.DateTimeField(auto_now_add=True)),
                ('service', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='tenant_services', to='services.service')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='tenant_services', to='tenants.tenant')),
            ],
            options={'db_table': 'tenant_services'},
        ),
        migrations.AlterUniqueTogether(
            name='tenantservice',
            unique_together={('tenant', 'service')},
        ),
        migrations.AddIndex(
            model_name='tenantservice',
            index=models.Index(fields=['tenant', 'status'], name='tenant_services_tenant_status_idx'),
        ),
    ]
