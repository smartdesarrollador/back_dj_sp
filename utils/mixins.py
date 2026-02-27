"""
DRF ViewSet mixins for tenant-aware views.
Stubs — full implementation in PASO 6.
"""
from rest_framework.viewsets import ModelViewSet


class TenantModelViewSet(ModelViewSet):
    """
    Base ViewSet that:
    - Filters all querysets by request.tenant automatically
    - Injects tenant into object creation automatically
    - Prevents cross-tenant data leaks

    Full implementation added in PASO 6 (RBAC middleware).
    """

    def get_queryset(self):
        qs = super().get_queryset()
        if hasattr(self.request, 'tenant') and self.request.tenant:
            qs = qs.filter(tenant=self.request.tenant)
        return qs

    def perform_create(self, serializer):
        if hasattr(self.request, 'tenant') and self.request.tenant:
            serializer.save(tenant=self.request.tenant)
        else:
            serializer.save()
