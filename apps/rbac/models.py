"""
Modelos RBAC: Permission, Role, RolePermission, UserRole.
"""
from django.db import models

from core.models import BaseModel


SCOPE_CHOICES = [
    ('all', 'All Resources'),
    ('own', 'Own Resources'),
    ('department', 'Department'),
    ('custom', 'Custom'),
]


class Permission(BaseModel):
    """
    Permiso atómico del sistema. Combinación única de resource + action.
    Ejemplo: resource='tasks', action='create' → codename='tasks.create'
    """
    codename = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    resource = models.CharField(max_length=50)
    action = models.CharField(max_length=50)

    class Meta:
        db_table = 'permissions'
        indexes = [
            models.Index(fields=['resource', 'action'], name='perm_resource_action_idx'),
        ]

    def __str__(self) -> str:
        return f"{self.codename}"


class Role(BaseModel):
    """
    Rol de usuario. Puede ser del sistema (is_system_role=True, tenant=None)
    o personalizado por tenant. Soporta herencia simple via inherits_from.
    """
    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='roles',
    )
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    is_system_role = models.BooleanField(default=False)
    inherits_from = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='inherited_by',
    )

    class Meta:
        db_table = 'roles'
        unique_together = [['tenant', 'name']]
        indexes = [
            models.Index(fields=['tenant', 'is_system_role'], name='roles_tenant_system_idx'),
            models.Index(fields=['is_system_role'], name='roles_system_role_idx'),
        ]

    def __str__(self) -> str:
        tenant_str = self.tenant.slug if self.tenant else 'system'
        return f"{self.name} ({tenant_str})"


class RolePermission(BaseModel):
    """
    Relación Many-to-Many entre Role y Permission con scope de acceso.
    """
    role = models.ForeignKey(
        'Role',
        on_delete=models.CASCADE,
        related_name='role_permissions',
    )
    permission = models.ForeignKey(
        'Permission',
        on_delete=models.CASCADE,
        related_name='role_permissions',
    )
    scope = models.CharField(max_length=20, choices=SCOPE_CHOICES, default='all')

    class Meta:
        db_table = 'role_permissions'
        unique_together = [['role', 'permission']]
        indexes = [
            models.Index(fields=['role', 'permission'], name='role_permissions_idx'),
        ]

    def __str__(self) -> str:
        return f"{self.role.name} → {self.permission.codename} ({self.scope})"


class UserRole(BaseModel):
    """
    Asignación de un Role a un User. Soporta expiración y auditoría de asignador.
    """
    user = models.ForeignKey(
        'auth_app.User',
        on_delete=models.CASCADE,
        related_name='user_roles',
    )
    role = models.ForeignKey(
        'Role',
        on_delete=models.CASCADE,
        related_name='user_roles',
    )
    assigned_by = models.ForeignKey(
        'auth_app.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_user_roles',
    )
    assigned_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'user_roles'
        unique_together = [['user', 'role']]
        indexes = [
            models.Index(fields=['user', 'role'], name='user_roles_user_role_idx'),
            models.Index(fields=['expires_at'], name='user_roles_expires_idx'),
        ]

    def __str__(self) -> str:
        return f"{self.user.email} → {self.role.name}"

    def is_expired(self) -> bool:
        """Retorna True si el rol asignado ya expiró."""
        if self.expires_at is None:
            return False
        from django.utils import timezone
        return timezone.now() > self.expires_at
