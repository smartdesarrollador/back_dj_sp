from django.db import models

from core.models import BaseModel


class ContactMessage(BaseModel):
    STATUS_CHOICES = [
        ('new', 'Nuevo'),
        ('read', 'Leído'),
        ('archived', 'Archivado'),
    ]

    name       = models.CharField(max_length=255)
    email      = models.EmailField()
    phone      = models.CharField(max_length=30, blank=True)
    message    = models.TextField()
    status     = models.CharField(max_length=20, choices=STATUS_CHOICES, default='new', db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        db_table = 'contact_messages'
        ordering = ['-created_at']

    def __str__(self) -> str:
        return f'{self.name} <{self.email}>'
