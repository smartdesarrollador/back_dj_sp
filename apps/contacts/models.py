"""
Contacts models — address book with optional grouping.
"""
from django.conf import settings
from django.db import models

from core.models import BaseModel


class ContactGroup(BaseModel):
    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='contact_groups',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='contact_groups',
    )
    name = models.CharField(max_length=100)
    color = models.CharField(max_length=20, default='blue')

    class Meta:
        db_table = 'contact_groups'
        unique_together = [('user', 'name')]

    def __str__(self) -> str:
        return self.name


class Contact(BaseModel):
    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='contacts',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='contacts',
    )
    group = models.ForeignKey(
        ContactGroup,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='contacts',
    )
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    company = models.CharField(max_length=100, blank=True)
    job_title = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        db_table = 'contacts'
        ordering = ['last_name', 'first_name']
        indexes = [
            models.Index(fields=['tenant', 'user', 'group']),
            models.Index(fields=['tenant', 'user', 'last_name', 'first_name']),
        ]

    def __str__(self) -> str:
        return f'{self.first_name} {self.last_name}'.strip()
