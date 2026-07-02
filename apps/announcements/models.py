from django.db import models

from core.models import BaseModel

PLACEMENT_CHOICES = [
    ('home', 'Home'),
    ('dashboard', 'Dashboard'),
    ('both', 'Ambos'),
]


class Announcement(BaseModel):
    title = models.CharField(max_length=200)
    message = models.TextField(blank=True)
    image = models.ImageField(upload_to='announcements/', null=True, blank=True)
    cta_text = models.CharField(max_length=50, blank=True)
    cta_url = models.URLField(blank=True)
    placement = models.CharField(max_length=20, choices=PLACEMENT_CHOICES, default='both')
    is_active = models.BooleanField(default=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    priority = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'announcements'
        ordering = ['-priority', '-created_at']

    def __str__(self) -> str:
        return self.title
