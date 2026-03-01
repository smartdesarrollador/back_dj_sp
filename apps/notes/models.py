"""
Notes models — simple text notes with categories and pinning.
"""
from django.conf import settings
from django.db import models

from core.models import BaseModel


class Note(BaseModel):
    CATEGORY_CHOICES = [
        ('work', 'Work'),
        ('personal', 'Personal'),
        ('ideas', 'Ideas'),
        ('archive', 'Archive'),
    ]

    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='notes',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notes',
    )
    title = models.CharField(max_length=255)
    content = models.TextField(blank=True)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='personal')
    is_pinned = models.BooleanField(default=False)
    color = models.CharField(max_length=20, default='gray')

    class Meta:
        db_table = 'notes'
        ordering = ['-is_pinned', '-created_at']
        indexes = [
            models.Index(fields=['tenant', 'user', 'category']),
            models.Index(fields=['tenant', 'user', 'is_pinned']),
        ]

    def __str__(self) -> str:
        return self.title
