"""
Notes models — simple text notes with categories and pinning.
"""
from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models

from core.models import BaseModel


class NoteCategory(BaseModel):
    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='note_categories',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='note_categories',
    )
    name = models.CharField(max_length=100)
    color = models.CharField(max_length=20, default='blue')

    class Meta:
        db_table = 'note_categories'
        unique_together = [('user', 'name')]

    def __str__(self) -> str:
        return self.name


class Note(BaseModel):
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
    category = models.ForeignKey(
        NoteCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='notes',
    )
    tags = ArrayField(models.CharField(max_length=50), default=list, blank=True)
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
