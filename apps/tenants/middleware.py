"""
Tenant Middleware — stub for PASO 2.
Full implementation (subdomain extraction + PostgreSQL RLS) added in PASO 5.
"""


class TenantMiddleware:
    """
    Resolves the current tenant from request headers or JWT.
    Sets request.tenant and runs SET LOCAL app.tenant_id for RLS.

    Full implementation: PASO 5 (Auth JWT endpoints).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Stub: no tenant resolution yet — set to None
        request.tenant = None
        response = self.get_response(request)
        return response
