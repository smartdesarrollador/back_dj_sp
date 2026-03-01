"""
Support models — ticket management with comments.
"""
import uuid

from django.conf import settings
from django.db import models

from core.models import BaseModel


class SupportTicket(BaseModel):
    CATEGORY_CHOICES = [
        ('technical', 'Técnico'),
        ('billing', 'Facturación'),
        ('access', 'Acceso'),
        ('feature_request', 'Solicitud'),
        ('other', 'Otro'),
    ]
    STATUS_CHOICES = [
        ('open', 'Abierto'),
        ('in_progress', 'En Progreso'),
        ('waiting_client', 'Esperando Cliente'),
        ('resolved', 'Resuelto'),
        ('closed', 'Cerrado'),
    ]
    PRIORITY_CHOICES = [
        ('urgente', 'Urgente'),
        ('alta', 'Alta'),
        ('media', 'Media'),
        ('baja', 'Baja'),
    ]

    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='support_tickets',
    )
    client = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='submitted_tickets',
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_support_tickets',
    )
    reference = models.CharField(max_length=20, unique=True, blank=True, db_index=True)
    subject = models.CharField(max_length=255)
    description = models.TextField()
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES)
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='media')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open')
    client_email = models.EmailField(blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = f"TKT-{uuid.uuid4().hex[:8].upper()}"
        super().save(*args, **kwargs)

    class Meta:
        db_table = 'support_tickets'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tenant', 'status'], name='support_tickets_tenant_status_idx'),
            models.Index(fields=['tenant', 'priority'], name='support_tickets_tenant_priority_idx'),
            models.Index(fields=['tenant', 'client'], name='support_tickets_tenant_client_idx'),
            models.Index(fields=['assigned_to', 'status'], name='support_tickets_assigned_status_idx'),
        ]

    def __str__(self) -> str:
        return f"{self.reference} — {self.subject}"


class TicketComment(BaseModel):
    ROLE_CHOICES = [
        ('client', 'Cliente'),
        ('agent', 'Agente'),
    ]

    ticket = models.ForeignKey(
        SupportTicket,
        on_delete=models.CASCADE,
        related_name='comments',
    )
    author = models.CharField(max_length=255)
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)
    message = models.TextField()

    class Meta:
        db_table = 'ticket_comments'
        ordering = ['created_at']

    def __str__(self) -> str:
        return f"{self.role}: {self.author}"
