"""
Tenant model — organización raíz del sistema multi-tenant.
Cada tenant tiene su propio aislamiento de datos via FK o RLS (PASO 5).
"""
from django.db import models

from core.models import BaseModel
from utils.validators import validate_subdomain

PLAN_CHOICES = [
    ('free', 'Free'),
    ('starter', 'Starter'),
    ('professional', 'Professional'),
    ('enterprise', 'Enterprise'),
]


class Tenant(BaseModel):
    """
    Organización / cuenta raíz. Todos los recursos del sistema pertenecen a un tenant.
    Hereda UUID PK, created_at y updated_at de BaseModel.
    """
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)
    subdomain = models.CharField(
        max_length=63,
        unique=True,
        validators=[validate_subdomain],
    )
    plan = models.CharField(max_length=20, choices=PLAN_CHOICES, default='free')
    branding = models.JSONField(default=dict)   # {logo_url, primary_color, ...}
    settings = models.JSONField(default=dict)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'tenants'
        indexes = [
            models.Index(fields=['slug']),
            models.Index(fields=['subdomain']),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.slug})"
