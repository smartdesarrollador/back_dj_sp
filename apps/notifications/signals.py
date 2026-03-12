from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender='subscriptions.Invoice')
def on_invoice_paid(sender, instance, created, **kwargs):
    """Create a billing notification when an invoice is marked as paid."""
    if instance.status == 'paid':
        from apps.notifications.models import Notification
        Notification.objects.get_or_create(
            tenant=instance.tenant,
            category='billing',
            title=f'Nueva factura: {instance.amount_display}',
            defaults={
                'message': 'Tu factura está disponible para descarga.',
                'icon': 'CreditCard',
            },
        )


@receiver(post_save, sender='services.TenantService')
def on_tenant_service_suspended(sender, instance, created, **kwargs):
    """Create a services notification when a TenantService is suspended."""
    if not created and instance.status == 'suspended':
        from apps.notifications.models import Notification
        Notification.objects.get_or_create(
            tenant=instance.tenant,
            category='services',
            title=f'Servicio {instance.service.name} suspendido',
            defaults={
                'message': f'Tu servicio {instance.service.name} ha sido suspendido. Contacta soporte.',
                'icon': 'Layers',
            },
        )
