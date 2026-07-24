"""
Chat signals: enlazar invitaciones pendientes y limpiar archivos de adjuntos borrados.
"""
from django.conf import settings
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.chat.models import MessageAttachment


@receiver(post_delete, sender=MessageAttachment)
def delete_message_attachment_file(sender, instance, **kwargs) -> None:
    # Al borrar un mensaje, el CASCADE elimina la fila del adjunto pero no el binario del
    # disco: hay que borrarlo a mano para liberar la cuota de almacenamiento de verdad.
    if instance.file:
        instance.file.delete(save=False)


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
