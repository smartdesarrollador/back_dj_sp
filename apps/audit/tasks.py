"""
Celery tasks para mantenimiento del módulo AuditLog.
"""
from celery import shared_task
from datetime import timedelta
from django.utils import timezone

from utils.plans import PLAN_FEATURES


@shared_task(name='apps.audit.tasks.purge_old_audit_logs')
def purge_old_audit_logs() -> dict:
    """
    Purga logs de auditoría más antiguos que la ventana de retención del plan.
    Se ejecuta diariamente vía Celery Beat (2:00 AM UTC).
    """
    from apps.audit.models import AuditLog
    from apps.tenants.models import Tenant

    results = {}
    for tenant in Tenant.objects.filter(is_active=True).only('id', 'plan'):
        retention_days = PLAN_FEATURES.get(tenant.plan, PLAN_FEATURES['free']).get('audit_log_days', 7)
        cutoff = timezone.now() - timedelta(days=retention_days)
        deleted, _ = AuditLog.objects.filter(tenant=tenant, created_at__lt=cutoff).delete()
        if deleted:
            results[str(tenant.id)] = deleted
    return results
