from celery import shared_task


@shared_task
def check_trial_expiry():
    """Daily periodic task: notify tenants whose trial expires within 7 days."""
    from datetime import timedelta

    from django.utils import timezone

    from apps.notifications.models import Notification
    from apps.subscriptions.models import Subscription

    now = timezone.now()
    window_end = now + timedelta(days=7)

    subs = Subscription.objects.filter(
        status='trialing',
        trial_end__gte=now,
        trial_end__lte=window_end,
    ).select_related('tenant')

    for sub in subs:
        Notification.objects.get_or_create(
            tenant=sub.tenant,
            category='billing',
            title='Tu período de prueba vence pronto',
            defaults={
                'message': (
                    f'Tu prueba gratuita vence el {sub.trial_end.strftime("%d/%m/%Y")}. '
                    '¡Actualiza tu plan!'
                ),
                'icon': 'Clock',
            },
        )
