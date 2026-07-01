from django.db import models

from core.models import BaseModel


class CatalogItem(BaseModel):
    name = models.CharField(max_length=100)
    short_description = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to='catalog/', null=True, blank=True)
    icon_color = models.CharField(max_length=50, blank=True, default='#6366f1')
    category = models.CharField(max_length=50, blank=True)
    link_url = models.URLField(blank=True)
    badge_text = models.CharField(max_length=30, blank=True)
    target_apps = models.JSONField(default=list)
    is_active = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'catalog_items'
        ordering = ['order', 'created_at']

    def __str__(self) -> str:
        return self.name
