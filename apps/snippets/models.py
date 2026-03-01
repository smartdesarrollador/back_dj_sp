"""
CodeSnippet model — stores reusable code snippets with language and tags.
"""
from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models

from core.models import BaseModel


class CodeSnippet(BaseModel):
    LANGUAGE_CHOICES = [
        ('javascript', 'JavaScript'),
        ('typescript', 'TypeScript'),
        ('python', 'Python'),
        ('bash', 'Bash'),
        ('sql', 'SQL'),
        ('html', 'HTML'),
        ('css', 'CSS'),
        ('json', 'JSON'),
        ('yaml', 'YAML'),
        ('dockerfile', 'Dockerfile'),
        ('go', 'Go'),
        ('rust', 'Rust'),
        ('java', 'Java'),
        ('other', 'Other'),
    ]

    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='snippets',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='snippets',
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    code = models.TextField()
    language = models.CharField(
        max_length=20, choices=LANGUAGE_CHOICES, default='other'
    )
    tags = ArrayField(
        models.CharField(max_length=50), default=list, blank=True
    )

    class Meta:
        db_table = 'snippets'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tenant', 'user', 'language']),
            models.Index(fields=['tenant', 'user', 'created_at']),
        ]

    def __str__(self) -> str:
        return self.title
