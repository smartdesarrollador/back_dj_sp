"""
Mixins reutilizables para vistas APIView.
"""
from __future__ import annotations


class AuditMixin:
    """
    Mixin para vistas APIView que necesitan registrar eventos en AuditLog.
    Las vistas existentes (PASO 9, 10) conservan su código de audit directo.
    """

    def log_action(
        self,
        request,
        action: str,
        resource_type: str,
        resource_id: str = '',
        extra: dict | None = None,
    ) -> None:
        try:
            from apps.audit.models import AuditLog
            AuditLog.objects.create(
                tenant=request.tenant,
                user=request.user if request.user.is_authenticated else None,
                action=action,
                resource_type=resource_type,
                resource_id=str(resource_id),
                ip_address=request.META.get('REMOTE_ADDR'),
                user_agent=request.META.get('HTTP_USER_AGENT', ''),
                extra=extra or {},
            )
        except Exception:
            pass  # Audit failure must not block the response
