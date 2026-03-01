"""
Sharing models — resource sharing between users within the same tenant.

Hierarchy:
  Share  — tracks who shared what with whom (polymorphic by resource_type)
  SharePermission — static capability matrix per permission_level × resource_type
                    (populated via fixture apps/sharing/fixtures/share_permissions.json)
"""
from django.conf import settings
from django.db import models

from core.models import BaseModel


class Share(BaseModel):
    """
    A share grants a user access to a specific resource within a tenant.
    Supports project, section, and item resource types.
    Cascade shares (is_inherited=True) are auto-created for child resources
    when a project is shared.
    """
    RESOURCE_TYPES = [
        ('project', 'Project'),
        ('section', 'Section'),
        ('item', 'Item'),
    ]
    PERMISSION_LEVELS = [
        ('viewer', 'Viewer'),
        ('commenter', 'Commenter'),
        ('editor', 'Editor'),
        ('admin', 'Admin'),
    ]

    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='shares',
    )
    resource_type = models.CharField(max_length=50, choices=RESOURCE_TYPES)
    resource_id = models.UUIDField()
    shared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='shares_given',
    )
    shared_with = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='shares_received',
    )
    permission_level = models.CharField(
        max_length=20, choices=PERMISSION_LEVELS, default='viewer'
    )
    is_inherited = models.BooleanField(default=False)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'shares'
        unique_together = [('tenant', 'resource_type', 'resource_id', 'shared_with')]
        indexes = [
            models.Index(fields=['tenant', 'resource_type', 'resource_id']),
            models.Index(fields=['tenant', 'shared_with']),
        ]

    def __str__(self) -> str:
        return (
            f'{self.shared_with} → {self.resource_type}:{self.resource_id} '
            f'({self.permission_level})'
        )


class SharePermission(models.Model):
    """
    Static capability matrix: what each permission_level can do per resource_type.
    Populated via fixture apps/sharing/fixtures/share_permissions.json.
    No UUID PK or timestamps needed — purely reference data.
    """
    permission_level = models.CharField(max_length=20)
    resource_type = models.CharField(max_length=50)
    can_read = models.BooleanField(default=True)
    can_create = models.BooleanField(default=False)
    can_update = models.BooleanField(default=False)
    can_delete = models.BooleanField(default=False)
    can_share = models.BooleanField(default=False)

    class Meta:
        db_table = 'share_permissions'
        unique_together = [('permission_level', 'resource_type')]

    def __str__(self) -> str:
        return f'{self.permission_level} / {self.resource_type}'
