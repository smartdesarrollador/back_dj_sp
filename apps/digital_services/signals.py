"""
Signals de Digital Services.

Limpieza física de los archivos de DigitalAsset: Django borra la fila en los CASCADE
(al eliminar un PublicProfile o PortfolioItem) y en las tareas de GC, pero **no** borra
el binario del disco. Este receiver lo hace, para que el borrado libere cuota de verdad.
"""
from django.db.models.signals import post_delete
from django.dispatch import receiver

from apps.digital_services.models import DigitalAsset


@receiver(post_delete, sender=DigitalAsset)
def delete_digital_asset_file(sender, instance, **kwargs) -> None:
    if instance.file:
        instance.file.delete(save=False)
