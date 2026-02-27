"""
Migration inicial para el modelo User (custom AUTH_USER_MODEL).

Crea la tabla 'users' con:
- UUID como PK
- FK a 'tenants' tabla (on_delete=CASCADE) — depende de 0001 de tenants
- Campos de AbstractBaseUser: password, last_login
- Campos de PermissionsMixin: is_superuser, groups, user_permissions
- Campos propios: email (unique), name, avatar_url, email_verified,
  is_active, is_staff, mfa_enabled, mfa_secret, created_at, updated_at
- Índices en email y (tenant_id, email)

Operaciones: bajo riesgo (creación de tabla nueva).
Dependencia: apps.tenants 0001 debe aplicarse primero.
"""
import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    # Marca esta migration como la que provee AUTH_USER_MODEL.
    # Permite que otras apps usen settings.AUTH_USER_MODEL en sus dependencias.
    swappable = "AUTH_USER_MODEL"

    dependencies = [
        # Tenants debe existir primero — la FK en User la referencia
        ("tenants", "0001_initial_tenant_model"),
        # PermissionsMixin requiere contenttypes y auth
        ("contenttypes", "0002_remove_content_type_name"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.CreateModel(
            name="User",
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
                # AbstractBaseUser fields
                ("password", models.CharField(max_length=128, verbose_name="password")),
                (
                    "last_login",
                    models.DateTimeField(
                        blank=True,
                        null=True,
                        verbose_name="last login",
                    ),
                ),
                # PermissionsMixin fields
                (
                    "is_superuser",
                    models.BooleanField(
                        default=False,
                        help_text=(
                            "Designates that this user has all permissions "
                            "without explicitly assigning them."
                        ),
                        verbose_name="superuser status",
                    ),
                ),
                # FK al tenant
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="users",
                        to="tenants.tenant",
                    ),
                ),
                # Campos propios
                ("email", models.EmailField(max_length=254, unique=True)),
                ("name", models.CharField(max_length=255)),
                ("avatar_url", models.URLField(blank=True)),
                ("email_verified", models.BooleanField(default=False)),
                ("is_active", models.BooleanField(default=True)),
                ("is_staff", models.BooleanField(default=False)),
                ("mfa_enabled", models.BooleanField(default=False)),
                ("mfa_secret", models.CharField(blank=True, max_length=32)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                # PermissionsMixin M2M fields
                (
                    "groups",
                    models.ManyToManyField(
                        blank=True,
                        help_text=(
                            "The groups this user belongs to. A user will get all "
                            "permissions granted to each of their groups."
                        ),
                        related_name="user_set",
                        related_query_name="user",
                        to="auth.group",
                        verbose_name="groups",
                    ),
                ),
                (
                    "user_permissions",
                    models.ManyToManyField(
                        blank=True,
                        help_text="Specific permissions for this user.",
                        related_name="user_set",
                        related_query_name="user",
                        to="auth.permission",
                        verbose_name="user permissions",
                    ),
                ),
            ],
            options={
                "db_table": "users",
                "indexes": [
                    models.Index(fields=["email"], name="users_email_idx"),
                    models.Index(
                        fields=["tenant", "email"], name="users_tenant_email_idx"
                    ),
                ],
            },
        ),
    ]
