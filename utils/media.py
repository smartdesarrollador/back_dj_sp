"""Helpers para construir URLs absolutas de medios servidos por Django."""
from django.conf import settings


def build_media_url(field_file, request=None) -> str | None:
    """
    Devuelve la URL absoluta de un FileField/ImageField.

    Prioriza `APP_BASE_URL` (dominio público real) sobre `request.build_absolute_uri()`:
    cuando la petición llega a través de un rewrite/proxy que no reescribe el header Host
    (p.ej. los rewrites de Next.js hacia el contenedor Django interno), ese método devuelve
    un hostname que solo resuelve dentro de la red de Docker.
    """
    if not field_file:
        return None
    url = field_file.url
    if url.startswith(('http://', 'https://')):
        return url
    base = getattr(settings, 'APP_BASE_URL', '').rstrip('/')
    if base:
        return f'{base}{url}'
    if request:
        return request.build_absolute_uri(url)
    return url
