"""
Bookmarks models — URL bookmarks with tags (ArrayField) and collections.
"""
from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models

from core.models import BaseModel


class BookmarkCollection(BaseModel):
    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='bookmark_collections',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='bookmark_collections',
    )
    name = models.CharField(max_length=100)
    color = models.CharField(max_length=20, default='blue')

    class Meta:
        db_table = 'bookmark_collections'
        unique_together = [('user', 'name')]

    def __str__(self) -> str:
        return self.name


class Bookmark(BaseModel):
    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='bookmarks',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='bookmarks',
    )
    collection = models.ForeignKey(
        BookmarkCollection,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='bookmarks',
    )
    url = models.URLField(max_length=2048)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    tags = ArrayField(models.CharField(max_length=50), default=list, blank=True)
    favicon_url = models.URLField(blank=True)

    class Meta:
        db_table = 'bookmarks'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tenant', 'user', 'collection']),
            models.Index(fields=['tenant', 'user', 'created_at']),
        ]

    def __str__(self) -> str:
        return self.title
