"""
Projects models — credential vault with AES-256 field encryption.

Hierarchy:
  Project → ProjectSection → ProjectItem → ProjectItemField
  Project → ProjectMember (M2M with role)
"""
from django.conf import settings
from django.db import models

from core.models import BaseModel
from utils.encryption import encrypt_value


class Project(BaseModel):
    """
    Root credential vault owned by a tenant.
    """
    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='projects',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_projects',
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    color = models.CharField(max_length=7, default='#6366f1')  # hex color
    icon = models.CharField(max_length=50, blank=True)
    is_archived = models.BooleanField(default=False)

    class Meta:
        db_table = 'projects'
        ordering = ['-created_at']

    def __str__(self) -> str:
        return self.name


class ProjectSection(BaseModel):
    """
    Named group of credential items within a project.
    """
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='sections',
    )
    name = models.CharField(max_length=255)
    color = models.CharField(max_length=7, default='#6366f1')
    order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'project_sections'
        ordering = ['order']

    def __str__(self) -> str:
        return f'{self.project.name} / {self.name}'


class ProjectItem(BaseModel):
    """
    A credential entry (e.g. a website login) inside a section.
    """
    section = models.ForeignKey(
        ProjectSection,
        on_delete=models.CASCADE,
        related_name='items',
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    url = models.URLField(blank=True)
    username = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'project_items'
        ordering = ['order']

    def __str__(self) -> str:
        return self.name


class ProjectItemField(BaseModel):
    """
    A single labeled field (key-value) on a credential item.
    Password fields are auto-encrypted at save time.
    """
    FIELD_TYPES = [
        ('text', 'Text'),
        ('password', 'Password'),
        ('url', 'URL'),
        ('email', 'Email'),
        ('note', 'Note'),
    ]

    item = models.ForeignKey(
        ProjectItem,
        on_delete=models.CASCADE,
        related_name='fields',
    )
    label = models.CharField(max_length=100)
    value = models.TextField()
    field_type = models.CharField(max_length=20, choices=FIELD_TYPES, default='text')
    is_encrypted = models.BooleanField(default=False)

    class Meta:
        db_table = 'project_item_fields'

    def save(self, *args, **kwargs) -> None:
        if self.field_type == 'password' and not self.is_encrypted:
            self.value = encrypt_value(self.value)
            self.is_encrypted = True
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f'{self.item.name} / {self.label}'


class ProjectMember(BaseModel):
    """
    Explicit project membership with a role (viewer / editor / admin).
    """
    ROLES = [
        ('viewer', 'Viewer'),
        ('editor', 'Editor'),
        ('admin', 'Admin'),
    ]

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='members',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='project_memberships',
    )
    role = models.CharField(max_length=20, choices=ROLES, default='viewer')

    class Meta:
        db_table = 'project_members'
        unique_together = [('project', 'user')]

    def __str__(self) -> str:
        return f'{self.user} → {self.project.name} ({self.role})'
