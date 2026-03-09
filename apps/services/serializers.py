from rest_framework import serializers

from .models import Service

PLAN_ORDER: dict[str, int] = {
    'free': 0,
    'starter': 1,
    'professional': 2,
    'enterprise': 3,
}


class ServiceSerializer(serializers.ModelSerializer):
    available = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    redirect_url = serializers.SerializerMethodField()

    class Meta:
        model = Service
        fields = ['id', 'slug', 'name', 'description', 'icon',
                  'min_plan', 'available', 'status', 'redirect_url']

    def _get_tenant(self):
        return self.context['request'].tenant

    def get_available(self, obj: Service) -> bool:
        tenant = self._get_tenant()
        return PLAN_ORDER.get(tenant.plan, 0) >= PLAN_ORDER.get(obj.min_plan, 0)

    def get_status(self, obj: Service) -> str | None:
        """Returns TenantService.status if acquired by this tenant, else None."""
        tenant_services: dict = self.context.get('tenant_services', {})
        ts = tenant_services.get(obj.id)
        return ts.status if ts else None

    def get_redirect_url(self, obj: Service) -> str:
        tenant = self._get_tenant()
        return obj.url_template.format(subdomain=tenant.subdomain) + '/auth/sso'
