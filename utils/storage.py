"""
Cálculo del almacenamiento consumido por un tenant, para compararlo contra
utils.plans.PLAN_FEATURES[plan]['storage_gb'].

Solo cuenta archivos binarios subidos por el tenant (adjuntos de chat, logo/favicon,
comprobantes de pago Yape) — excluye contenido global de plataforma (releases, catálogo,
anuncios) y los campos cifrados de texto (Bóveda, env vars, SSH keys, SSL certs).
"""
from django.db.models import Sum


def get_tenant_storage_bytes(tenant) -> int:
    """Suma en bytes de todo lo que cuenta como almacenamiento del tenant."""
    from apps.chat.models import MessageAttachment
    from apps.subscriptions.models import YapePaymentProof

    total = MessageAttachment.objects.filter(
        message__sender__tenant=tenant
    ).aggregate(total=Sum('size'))['total'] or 0

    if tenant.logo:
        total += tenant.logo.size
    if tenant.favicon:
        total += tenant.favicon.size

    for proof in YapePaymentProof.objects.filter(subscription__tenant=tenant).only('screenshot'):
        total += proof.screenshot.size

    return total
