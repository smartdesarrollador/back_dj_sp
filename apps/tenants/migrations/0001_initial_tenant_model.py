"""
Migration inicial para el modelo Tenant.

Crea la tabla 'tenants' con:
- UUID como PK (sin lock de secuencia, seguro en tablas grandes)
- Campos de auditoría: created_at, updated_at
- Campos de negocio: name, slug, subdomain, plan, branding, settings, is_active
- Índices en slug y subdomain para búsquedas rápidas

Operaciones: bajo riesgo (creación de tabla nueva).
"""
import uuid

import utils.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Tenant",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                        db_index=True,
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=255)),
                ("slug", models.SlugField(unique=True)),
                (
                    "subdomain",
                    models.CharField(
                        max_length=63,
                        unique=True,
                        validators=[utils.validators.validate_subdomain],
                    ),
                ),
                (
                    "plan",
                    models.CharField(
                        choices=[
                            ("free", "Free"),
                            ("starter", "Starter"),
                            ("professional", "Professional"),
                            ("enterprise", "Enterprise"),
                        ],
                        default="free",
                        max_length=20,
                    ),
                ),
                ("branding", models.JSONField(default=dict)),
                ("settings", models.JSONField(default=dict)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={
                "db_table": "tenants",
                "indexes": [
                    models.Index(fields=["slug"], name="tenants_slug_idx"),
                    models.Index(fields=["subdomain"], name="tenants_subdomain_idx"),
                ],
            },
        ),
    ]
