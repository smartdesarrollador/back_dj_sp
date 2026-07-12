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


class IsStaffUser(BasePermission):
    """
    Restricts an endpoint to platform staff (user.is_staff), regardless of any
    RBAC permission the requesting user's role happens to carry.

    Needed because RBAC permission codenames like 'customers.read' are also
    granted to the tenant-scoped system 'Owner' role (so Owner can manage
    their *own* tenant's users/billing/roles through the same /admin/ API
    namespace). Views that return or mutate data belonging to OTHER tenants
    (e.g. the client/tenant list) must never rely on HasPermission alone —
    compose both: permission_classes = [IsStaffUser, HasPermission(...)].
    Superusers are always staff-equivalent (they already bypass RBAC checks).
    """
    message = {
        'error': {
            'code': 'staff_required',
            'message': 'This endpoint is restricted to platform staff.',
        }
    }

    def has_permission(self, request, view) -> bool:
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and (user.is_staff or user.is_superuser)
        )


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
            tenant = getattr(request, 'tenant', None)
            # Auth endpoints bypass TenantMiddleware; fall back to the
            # authenticated user's own tenant for plan checks.
            if tenant is None and getattr(request, 'user', None) and request.user.is_authenticated:
                tenant = getattr(request.user, 'tenant', None)
            if tenant is None:
                return True
            if not plan_has_feature(tenant.plan, feature):
                raise FeatureNotAvailable(
                    detail=f'La funcionalidad "{feature}" no está disponible en tu plan actual.'
                )
            return True

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


def check_storage_limit(tenant, additional_bytes: int) -> None:
    """
    Verifica que sumar `additional_bytes` al almacenamiento ya consumido no supere
    el storage_gb del plan del tenant.

    A diferencia de check_plan_limit (conteo de unidades vía max_{resource}), storage_gb
    no sigue esa convención de nombre en PLAN_FEATURES — se lee vía get_effective_plan_limits,
    que también aplica el override editable desde el Admin si existe (Plan.limits).

    Args:
        tenant: instancia de Tenant
        additional_bytes: tamaño del archivo que se está por subir

    Raises:
        PlanLimitExceeded: HTTP 402 si (uso actual + additional_bytes) > límite del plan
    """
    from utils.plans import get_effective_plan_limits
    from utils.storage import get_tenant_storage_bytes

    limit_gb = get_effective_plan_limits(tenant.plan).get('storage_gb')
    if limit_gb is None:
        return  # Ilimitado (Enterprise)

    limit_bytes = limit_gb * 1024 ** 3
    current_bytes = get_tenant_storage_bytes(tenant)
    if current_bytes + additional_bytes > limit_bytes:
        raise PlanLimitExceeded(
            detail=(
                f'Has alcanzado el límite de almacenamiento de {limit_gb} GB para el plan '
                f'{tenant.plan}. Actualiza tu plan para continuar.'
            )
        )
