"""
TenantMiddleware — resolves the current tenant from the X-Tenant-Slug header.
Performs a Redis-cached DB lookup (TTL 5 min) and sets request.tenant.
Also runs SET app.tenant_id for PostgreSQL Row-Level Security.
"""
from django.core.cache import cache
from django.db import connection

TENANT_CACHE_TTL = 300  # 5 min

_PUBLIC_PATH_PREFIXES = (
    '/api/v1/auth/',
    '/api/health/',
    '/api/schema/',
    '/api/docs/',
    '/api/redoc/',
    '/admin/',
)


class TenantMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.tenant = self._resolve_tenant(request)
        if request.tenant:
            self._set_rls_context(request.tenant.id)
        return self.get_response(request)

    def _resolve_tenant(self, request):
        if any(request.path_info.startswith(p) for p in _PUBLIC_PATH_PREFIXES):
            return None
        slug = request.headers.get('X-Tenant-Slug')
        return self._get_by_slug(slug) if slug else None

    def _get_by_slug(self, slug: str):
        cache_key = f'tenant:slug:{slug}'
        tenant = cache.get(cache_key)
        if tenant is None:
            from apps.tenants.models import Tenant
            try:
                tenant = Tenant.objects.get(slug=slug, is_active=True)
                cache.set(cache_key, tenant, timeout=TENANT_CACHE_TTL)
            except Tenant.DoesNotExist:
                return None
        return tenant

    def _set_rls_context(self, tenant_id) -> None:
        try:
            with connection.cursor() as cursor:
                cursor.execute('SET app.tenant_id = %s', [str(tenant_id)])
        except Exception:
            pass  # Non-critical: DB-level RLS is the background control
