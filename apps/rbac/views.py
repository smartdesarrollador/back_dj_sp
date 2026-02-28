"""
RBAC views.

Endpoints:
  GET /api/v1/features/  → Features y límites del plan activo del tenant
"""
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from utils.plans import PLAN_FEATURES

# Claves que son límites operacionales (no feature flags booleanos)
_OPERATIONAL_LIMITS = {'audit_log_days', 'storage_gb', 'api_calls_per_month'}


class FeaturesView(APIView):
    """
    Retorna las features y límites del plan activo del tenant autenticado.

    Response:
        {
            "plan": "professional",
            "features": {
                "custom_roles": true,
                "mfa": true,
                ...
            },
            "limits": {
                "users": 25,
                "projects": null,
                "storage_gb": 20,
                "api_calls_per_month": 100000
            }
        }
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tenant = getattr(request, 'tenant', None)
        plan = tenant.plan if tenant else 'free'
        plan_config = PLAN_FEATURES.get(plan, PLAN_FEATURES['free'])

        feature_flags = {
            k: v
            for k, v in plan_config.items()
            if not k.startswith('max_') and k not in _OPERATIONAL_LIMITS
        }

        limits = {
            'users': plan_config.get('max_users'),
            'projects': plan_config.get('max_projects'),
            'storage_gb': plan_config.get('storage_gb'),
            'api_calls_per_month': plan_config.get('api_calls_per_month'),
        }

        return Response({'plan': plan, 'features': feature_flags, 'limits': limits})
