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
        # El tenant sale del header (request.tenant) o, si no vino (p.ej. Vista, que autentica
        # solo con Bearer sin X-Tenant-Slug), del usuario autenticado. Sin tenant no hay a quién
        # atribuir el evento y AuditLog.tenant es NOT NULL: se omite en vez de reventar la
        # transacción con una IntegrityError.
        is_authed = request.user.is_authenticated
        tenant = getattr(request, 'tenant', None) or (getattr(request.user, 'tenant', None)
                                                       if is_authed else None)
        if tenant is None:
            return
        try:
            from apps.audit.models import AuditLog
            AuditLog.objects.create(
                tenant=tenant,
                user=request.user if is_authed else None,
                action=action,
                resource_type=resource_type,
                resource_id=str(resource_id),
                ip_address=request.META.get('REMOTE_ADDR'),
                user_agent=request.META.get('HTTP_USER_AGENT', ''),
                extra=extra or {},
            )
        except Exception:
            pass  # Audit failure must not block the response
