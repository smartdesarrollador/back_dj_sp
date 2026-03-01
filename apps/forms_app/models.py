"""
Forms models — form builder with questions and responses.
"""
import uuid

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models

from core.models import BaseModel


class Form(BaseModel):
    STATUS_CHOICES = [
        ('draft', 'Borrador'),
        ('active', 'Activo'),
        ('closed', 'Cerrado'),
    ]

    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='forms',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='forms',
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    public_url_slug = models.SlugField(unique=True, blank=True)
    response_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'forms'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tenant', 'user', 'status']),
        ]

    def save(self, *args, **kwargs):
        if not self.public_url_slug:
            self.public_url_slug = uuid.uuid4().hex[:12]
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.title


class FormQuestion(models.Model):
    TYPES = [
        ('text', 'Texto'),
        ('multiple_choice', 'Opción múltiple'),
        ('checkbox', 'Casillas'),
        ('number', 'Número'),
        ('date', 'Fecha'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    form = models.ForeignKey(Form, on_delete=models.CASCADE, related_name='questions')
    order = models.PositiveSmallIntegerField(default=0)
    label = models.CharField(max_length=255)
    question_type = models.CharField(max_length=30, choices=TYPES)
    options = ArrayField(models.CharField(max_length=100), default=list, blank=True)
    required = models.BooleanField(default=False)

    class Meta:
        db_table = 'form_questions'
        ordering = ['order']

    def __str__(self) -> str:
        return self.label


class FormResponse(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    form = models.ForeignKey(Form, on_delete=models.CASCADE, related_name='responses')
    data = models.JSONField()
    respondent_ip = models.GenericIPAddressField(blank=True, null=True)
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'form_responses'
        ordering = ['-submitted_at']
        indexes = [
            models.Index(fields=['form', 'submitted_at']),
        ]

    def __str__(self) -> str:
        return f'Response to {self.form_id} at {self.submitted_at}'
