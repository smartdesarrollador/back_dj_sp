from django.contrib.postgres.fields import ArrayField
from django.db import models

from core.models import BaseModel


class ChatKnowledgeArticle(BaseModel):
    CATEGORY_CHOICES = [
        ('general', 'General'),
        ('pricing', 'Precios y Planes'),
        ('features', 'Características'),
        ('onboarding', 'Primeros Pasos'),
        ('faq', 'Preguntas Frecuentes'),
        ('support', 'Soporte'),
    ]

    title = models.CharField(max_length=200)
    content = models.TextField()
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default='general')
    keywords = ArrayField(models.CharField(max_length=100), default=list, blank=True)
    is_active = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order', 'title']
        indexes = [
            models.Index(fields=['category', 'is_active']),
            models.Index(fields=['is_active', 'order']),
        ]

    def __str__(self) -> str:
        return self.title


class ChatSession(BaseModel):
    session_token = models.CharField(max_length=64, unique=True, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, blank=True)
    message_count = models.PositiveIntegerField(default=0)
    converted = models.BooleanField(default=False)
    last_activity_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['created_at']),
        ]

    def __str__(self) -> str:
        return f'Session {self.session_token[:12]}… ({self.message_count} msgs)'


class ChatMessage(BaseModel):
    ROLE_CHOICES = [
        ('user', 'User'),
        ('assistant', 'Assistant'),
    ]

    session = models.ForeignKey(ChatSession, on_delete=models.CASCADE, related_name='messages')
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)
    content = models.TextField()
    tokens_used = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self) -> str:
        return f'[{self.role}] {self.content[:60]}'
