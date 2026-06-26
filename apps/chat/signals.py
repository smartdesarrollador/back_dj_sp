"""
Link pending email-based chat invitations to a user when they register.
"""
from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def link_pending_chat_invitations(sender, instance, created, **kwargs):
    if not created or not instance.email:
        return
    from apps.chat.models import ChatConnection

    ChatConnection.objects.filter(
        invited_email__iexact=instance.email, addressee__isnull=True
    ).update(
        addressee=instance,
        addressee_tenant=instance.tenant,
        invited_email='',
    )
