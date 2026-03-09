from django.db import models

from core.models import BaseModel


class Service(BaseModel):
    slug = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=50)
    url_template = models.CharField(max_length=255)
    min_plan = models.CharField(max_length=20, default='free')
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'services'

    def __str__(self) -> str:
        return self.name


class TenantService(BaseModel):
    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='tenant_services',
    )
    service = models.ForeignKey(
        Service,
        on_delete=models.CASCADE,
        related_name='tenant_services',
    )
    status = models.CharField(max_length=20, default='active')
    acquired_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'tenant_services'
        unique_together = [['tenant', 'service']]
        indexes = [models.Index(fields=['tenant', 'status'])]

    def __str__(self) -> str:
        return f'{self.tenant} - {self.service}'
