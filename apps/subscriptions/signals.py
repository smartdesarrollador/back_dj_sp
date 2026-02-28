"""
Auto-create a Subscription when a Tenant is created.
Uses get_or_create — idempotent, safe to call multiple times.
"""
from datetime import timedelta

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone


@receiver(post_save, sender='tenants.Tenant')
def auto_create_subscription(sender, instance, created, **kwargs):
    if created:
        from apps.subscriptions.models import Subscription
        Subscription.objects.get_or_create(
            tenant=instance,
            defaults={
                'plan': 'free',
                'status': 'trialing',
                'trial_start': timezone.now(),
                'trial_end': timezone.now() + timedelta(days=14),
            },
        )
