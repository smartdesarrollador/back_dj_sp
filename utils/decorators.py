"""
Utility decorators for RBAC permission and plan checks.
Funciona en métodos de APIView (self, request, *args, **kwargs).
"""
import functools
from typing import Callable

from rest_framework.exceptions import NotAuthenticated, PermissionDenied


def require_permission(codename: str) -> Callable:
    """
    Decorator: requires user to have the given permission codename.

    Usage:
        class MyView(APIView):
            @require_permission('projects.create')
            def post(self, request):
                ...
    """
    def decorator(view_method: Callable) -> Callable:
        @functools.wraps(view_method)
        def wrapper(view_instance, request, *args, **kwargs):
            if not request.user or not request.user.is_authenticated:
                raise NotAuthenticated()

            # Importación tardía para evitar ciclos circulares
            from apps.rbac.permissions import _user_has_permission
            if not _user_has_permission(request.user, codename):
                raise PermissionDenied({
                    'code': 'permission_denied',
                    'message': f'Permission required: {codename}',
                    'required_permission': codename,
                })
            return view_method(view_instance, request, *args, **kwargs)

        wrapper._required_permission = codename
        return wrapper
    return decorator


def require_feature(feature: str) -> Callable:
    """
    Decorator: requires tenant to have the given plan feature enabled.

    Usage:
        class MyView(APIView):
            @require_feature('custom_roles')
            def post(self, request):
                ...
    """
    def decorator(view_method: Callable) -> Callable:
        @functools.wraps(view_method)
        def wrapper(view_instance, request, *args, **kwargs):
            tenant = getattr(request, 'tenant', None)
            if tenant is not None:
                from utils.plans import plan_has_feature
                from core.exceptions import FeatureNotAvailable
                if not plan_has_feature(tenant.plan, feature):
                    raise FeatureNotAvailable(
                        detail=(
                            f'La funcionalidad "{feature}" no está disponible en el '
                            f'plan {tenant.plan}. Actualiza tu plan para acceder.'
                        )
                    )
            return view_method(view_instance, request, *args, **kwargs)

        wrapper._required_feature = feature
        return wrapper
    return decorator


def check_plan_limit(resource: str, count_fn: Callable) -> Callable:
    """
    Decorator: checks plan-based resource limits before allowing creation.

    Args:
        resource: nombre del recurso sin prefijo 'max_' (ej. 'projects')
        count_fn: callable que recibe request y retorna el conteo actual (int)

    Usage:
        class MyView(APIView):
            @check_plan_limit('projects', lambda req: Project.objects.filter(tenant=req.tenant).count())
            def post(self, request):
                ...
    """
    def decorator(view_method: Callable) -> Callable:
        @functools.wraps(view_method)
        def wrapper(view_instance, request, *args, **kwargs):
            current = count_fn(request)
            from apps.rbac.permissions import check_plan_limit as _check_plan_limit
            _check_plan_limit(request.user, resource, current)
            return view_method(view_instance, request, *args, **kwargs)

        wrapper._plan_limit_resource = resource
        return wrapper
    return decorator
