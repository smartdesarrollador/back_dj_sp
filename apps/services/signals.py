from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender='tenants.Tenant')
def provision_free_services(sender: type, instance: object, created: bool, **kwargs: object) -> None:
    if not created:
        return
    from apps.services.models import Service, TenantService

    for service in Service.objects.filter(min_plan='free', is_active=True):
        TenantService.objects.get_or_create(
            tenant=instance,
            service=service,
            defaults={'status': 'active'},
        )
