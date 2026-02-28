"""
DRF permission classes para RBAC y feature gates.

Provides:
  - HasPermission(codename) → clase DRF (factory)
  - HasFeature(feature)     → clase DRF (factory)
  - check_plan_limit(user, resource, current_count) → None o lanza PlanLimitExceeded
"""
from django.core.cache import cache
from django.utils import timezone
from rest_framework.permissions import BasePermission

from core.exceptions import FeatureNotAvailable, PlanLimitExceeded
from utils.plans import get_plan_limit, plan_has_feature

_PERM_CACHE_TTL = 300  # segundos


# ─── Función privada de verificación ──────────────────────────────────────────

def _user_has_permission(user, codename: str) -> bool:
    """
    Verifica si un usuario tiene el permiso indicado por codename.

    Flujo:
    1. Superusuario → siempre True
    2. Cache Redis: 'rbac:perm:{user_id}:{codename}'
    3. Query: roles activos (no expirados) → role_permissions → permission.codename
    4. Herencia de roles (máximo 3 niveles para prevenir ciclos)
    5. Cachea resultado 300s
    """
    if user.is_superuser:
        return True

    cache_key = f'rbac:perm:{user.pk}:{codename}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    result = _check_permission_in_db(user, codename)
    cache.set(cache_key, result, _PERM_CACHE_TTL)
    return result


def _check_permission_in_db(user, codename: str) -> bool:
    """Consulta DB para verificar permiso, incluyendo herencia de roles."""
    from apps.rbac.models import UserRole

    now = timezone.now()
    active_user_roles = UserRole.objects.filter(
        user=user,
    ).filter(
        models_expires_at_filter(now)
    ).select_related('role__inherits_from')

    role_ids_to_check = set()
    for user_role in active_user_roles:
        role_ids_to_check.update(_collect_role_ids(user_role.role, depth=3))

    if not role_ids_to_check:
        return False

    from apps.rbac.models import RolePermission
    return RolePermission.objects.filter(
        role_id__in=role_ids_to_check,
        permission__codename=codename,
    ).exists()


def models_expires_at_filter(now):
    """Construye el filtro Q para roles no expirados."""
    from django.db.models import Q
    return Q(expires_at__isnull=True) | Q(expires_at__gt=now)


def _collect_role_ids(role, depth: int) -> set:
    """
    Recopila IDs del rol y sus ancestros por herencia.
    depth limita la recursión para prevenir ciclos (máx 3 niveles).
    """
    if role is None or depth <= 0:
        return set()
    ids = {role.pk}
    if role.inherits_from_id:
        parent = role.inherits_from
        if parent:
            ids |= _collect_role_ids(parent, depth - 1)
    return ids


# ─── Permission Factories ──────────────────────────────────────────────────────

def HasPermission(codename: str) -> type[BasePermission]:
    """
    Factory que retorna una clase DRF Permission que verifica un codename.

    Usage:
        permission_classes = [HasPermission('projects.create')]
    """
    class _Permission(BasePermission):
        message = {
            'error': {
                'code': 'permission_denied',
                'message': f'Permission required: {codename}',
                'required_permission': codename,
            }
        }

        def has_permission(self, request, view) -> bool:
            return (
                request.user
                and request.user.is_authenticated
                and _user_has_permission(request.user, codename)
            )

    _Permission.__name__ = f'HasPermission[{codename}]'
    _Permission.__qualname__ = f'HasPermission[{codename}]'
    return _Permission


def HasFeature(feature: str) -> type[BasePermission]:
    """
    Factory que retorna una clase DRF Permission que verifica un feature flag de plan.

    Si no hay request.tenant (endpoints públicos/auth), permite el acceso.

    Usage:
        permission_classes = [HasFeature('custom_roles')]
    """
    class _Feature(BasePermission):
        message = {
            'error': {
                'code': 'feature_not_available',
                'message': f'Feature not available on your current plan: {feature}',
                'upgrade_url': '/billing/upgrade',
            }
        }

        def has_permission(self, request, view) -> bool:
            if not hasattr(request, 'tenant') or request.tenant is None:
                return True
            return plan_has_feature(request.tenant.plan, feature)

    _Feature.__name__ = f'HasFeature[{feature}]'
    _Feature.__qualname__ = f'HasFeature[{feature}]'
    return _Feature


# ─── Plan Limit Check ──────────────────────────────────────────────────────────

def check_plan_limit(user, resource: str, current_count: int) -> None:
    """
    Verifica que el count actual no supere el límite del plan del tenant.

    Args:
        user: instancia de User (debe tener user.tenant.plan)
        resource: nombre del recurso sin prefijo 'max_' (ej. 'projects')
        current_count: cantidad actual de recursos existentes

    Raises:
        PlanLimitExceeded: HTTP 402 si current_count >= limit y limit no es None
    """
    try:
        plan = user.tenant.plan
    except AttributeError:
        return  # Sin tenant → no aplicar límite

    limit = get_plan_limit(plan, resource)
    if limit is None:
        return  # Ilimitado

    if current_count >= limit:
        raise PlanLimitExceeded(
            detail=(
                f'Has alcanzado el límite de {limit} {resource} para el plan {plan}. '
                'Actualiza tu plan para continuar.'
            )
        )
