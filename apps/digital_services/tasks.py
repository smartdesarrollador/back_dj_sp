"""
Celery tasks de mantenimiento de Digital Services.
"""
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

# Slots cuyas referencias (campos URL) sabemos rastrear por completo. landing_image y
# cv_photo se excluyen a propósito: sus ubicaciones (secciones JSON de landing / foto de CV)
# aún no están cableadas, así que recolectarlos podría borrar imágenes en uso.
_COLLECTABLE_SLOTS = ('avatar', 'og_image', 'portfolio_cover', 'portfolio_gallery')

_ORPHAN_MIN_AGE = timedelta(hours=24)


def _referenced_blob() -> str:
    """
    Concatenación de todas las URLs de imágenes referenciadas en Vista. Un asset se considera
    en uso si el nombre de su archivo aparece como substring de este blob (host-agnóstico).
    """
    from apps.digital_services.models import PortfolioItem, PublicProfile

    parts: list[str] = []
    for avatar_url, og_image_url in PublicProfile.objects.values_list('avatar_url', 'og_image_url'):
        parts.append(avatar_url or '')
        parts.append(og_image_url or '')

    for cover_url, gallery in PortfolioItem.objects.values_list('cover_image_url', 'gallery_images'):
        parts.append(cover_url or '')
        if gallery:
            parts.extend(str(url) for url in gallery)

    return '\n'.join(parts)


@shared_task(name='apps.digital_services.tasks.collect_orphan_digital_assets')
def collect_orphan_digital_assets() -> dict:
    """
    Borra los DigitalAsset no referenciados con más de 24 h de antigüedad, liberando cuota.
    Se ejecuta diariamente vía Celery Beat. El post_delete del modelo borra el archivo físico.
    """
    from apps.digital_services.models import DigitalAsset

    cutoff = timezone.now() - _ORPHAN_MIN_AGE
    blob = _referenced_blob()

    deleted = 0
    candidates = DigitalAsset.objects.filter(
        slot__in=_COLLECTABLE_SLOTS, created_at__lt=cutoff
    )
    for asset in candidates.iterator():
        if asset.file.name and asset.file.name not in blob:
            asset.delete()  # dispara post_delete → borra el binario
            deleted += 1

    return {'deleted': deleted}
