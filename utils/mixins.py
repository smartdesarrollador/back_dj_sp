"""
DRF ViewSet mixins for tenant-aware views.
"""
from rest_framework.viewsets import ModelViewSet


class TenantModelViewSet(ModelViewSet):
    """
    Base ViewSet que:
    - Filtra todos los querysets por request.tenant automáticamente
    - Inyecta tenant en la creación de objetos automáticamente
    - Previene fugas de datos cross-tenant
    - Incluye request.tenant en el contexto del serializer
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

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        if hasattr(self.request, 'tenant'):
            ctx['tenant'] = self.request.tenant
        return ctx
