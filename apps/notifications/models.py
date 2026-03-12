from django.db import models

from core.models import BaseModel

CATEGORY_CHOICES = [
    ('security', 'Seguridad'),
    ('billing', 'Facturación'),
    ('system', 'Sistema'),
    ('users', 'Usuarios'),
    ('roles', 'Roles'),
    ('services', 'Servicios'),
]


class Notification(BaseModel):
    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='notifications',
    )
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    title = models.CharField(max_length=200)
    message = models.TextField(blank=True)
    icon = models.CharField(max_length=50, blank=True)
    read = models.BooleanField(default=False)

    class Meta:
        db_table = 'notifications'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tenant', 'read']),
            models.Index(fields=['tenant', 'category']),
        ]

    def __str__(self) -> str:
        return f'[{self.category}] {self.title}'
