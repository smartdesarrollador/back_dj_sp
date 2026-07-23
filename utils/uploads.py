"""
Validación centralizada de archivos subidos.

Fuente única de verdad para **qué** se puede subir (extensión + tipo real por contenido)
y **cuánto** puede pesar (tope del plan del tenant + tope duro de infraestructura).

Principio de separación:
  - El **peso** es palanca comercial → por plan, editable desde el Admin (Plan.limits).
  - Los **tipos** son política de seguridad → globales, en código, no editables. Habilitar
    SVG/HTML desde un formulario abriría XSS almacenado, agravado por que MEDIA_ROOT se
    sirve sin autenticación (config/urls.py).
  - El **tope duro** por categoría acota siempre al valor del plan, para que un override
    del Admin no pueda llenar el disco (DATA_UPLOAD_MAX_MEMORY_SIZE son 600 MB).

Uso:
    from utils.uploads import validate_upload

    validate_upload(request.FILES['file'], category='chat_attachment', tenant=request.tenant)

Ver `prd/features/limites-archivos-por-plan.md`.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from PIL import Image, UnidentifiedImageError
from rest_framework.exceptions import ValidationError

# Los mensajes de ValidationError van SIEMPRE como lista ({'file': ['...']}), no como
# string suelto: core.exceptions._get_message solo sabe extraer el texto de valores lista
# (la forma que usan los errores de campo de DRF). Con un string plano el cliente recibe
# un genérico "Validation error" y se pierde el motivo real del rechazo.
MB = 1024 * 1024

# Píxeles máximos que Pillow acepta decodificar. Acota decompression bombs: una imagen de
# pocos KB puede declarar dimensiones gigantescas y agotar la RAM al abrirla.
MAX_IMAGE_PIXELS = 50_000_000

# Bytes iniciales que se leen para identificar el tipo real por firma.
_SNIFF_BYTES = 16


@dataclass(frozen=True)
class UploadCategory:
    """Política de subida para un tipo de recurso."""

    extensions: frozenset[str]
    hard_max_mb: int
    # Clave de PLAN_FEATURES que fija el tope; None → el tope es fixed_max_mb para todos los planes.
    plan_key: str | None = None
    fixed_max_mb: int | None = None
    # Si el archivo suma a la cuota storage_gb del tenant (ver utils/storage.py).
    counts_toward_storage: bool = False


UPLOAD_CATEGORIES: dict[str, UploadCategory] = {
    'chat_attachment': UploadCategory(
        extensions=frozenset({
            '.png', '.jpg', '.jpeg', '.webp', '.gif',
            '.pdf', '.txt', '.csv', '.zip', '.docx', '.xlsx',
        }),
        plan_key='max_file_upload_mb',
        hard_max_mb=100,
        counts_toward_storage=True,
    ),
    'tenant_branding': UploadCategory(
        extensions=frozenset({'.png', '.jpg', '.jpeg', '.webp', '.ico'}),
        plan_key='max_image_upload_mb',
        hard_max_mb=10,
        counts_toward_storage=True,
    ),
    # Deliberadamente NO gateado por plan NI por cuota: con el tope de imagen de Free (2 MB) un
    # screenshot de móvil (3-8 MB) bloquearía al cliente justo al intentar pagar su upgrade, y con
    # la cuota llena no podría pagar el plan que le daría más espacio. El comprobante sigue sumando
    # al total en utils/storage.py; lo que no hace es impedir su propia subida.
    'payment_proof': UploadCategory(
        extensions=frozenset({'.png', '.jpg', '.jpeg', '.webp'}),
        fixed_max_mb=10,
        hard_max_mb=10,
    ),
    # Contenido global de plataforma (solo staff): no depende del plan ni suma a la cuota.
    'platform_image': UploadCategory(
        extensions=frozenset({'.png', '.jpg', '.jpeg', '.webp'}),
        fixed_max_mb=2,
        hard_max_mb=5,
    ),
    'desktop_release': UploadCategory(
        extensions=frozenset({'.exe', '.msi', '.dmg'}),
        fixed_max_mb=500,
        hard_max_mb=500,
    ),
}

# Extensiones que se validan abriéndolas con Pillow en vez de por firma de bytes.
_IMAGE_EXTENSIONS = frozenset({'.png', '.jpg', '.jpeg', '.webp', '.gif', '.ico', '.bmp', '.tiff'})

# Firmas (magic bytes) de los formatos no-imagen aceptados. Una extensión puede tener varias
# firmas válidas (p.ej. un zip vacío o segmentado).
_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    '.pdf': (b'%PDF-',),
    # docx/xlsx son contenedores ZIP (OOXML), misma firma que un .zip normal.
    '.zip': (b'PK\x03\x04', b'PK\x05\x06', b'PK\x07\x08'),
    '.docx': (b'PK\x03\x04',),
    '.xlsx': (b'PK\x03\x04',),
    '.exe': (b'MZ',),
    '.msi': (b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1',),  # OLE2 compound file
    '.dmg': (b'koly', b'\x78\x01\x73\x0d\x62\x62\x60'),
}

# Extensiones de texto plano: no tienen firma, se validan por decodificación UTF-8.
_TEXT_EXTENSIONS = frozenset({'.txt', '.csv'})


def is_image(file: Any) -> bool:
    """
    True si el archivo es una imagen, según su extensión.

    Solo es fiable **después** de `validate_upload()`, que es quien comprueba que el
    contenido corresponde de verdad a la extensión. Antes de eso, la extensión es un
    dato del cliente igual de falsificable que el `content_type`.
    """
    return _get_extension(file) in _IMAGE_EXTENSIONS


def _label(extensions: frozenset[str]) -> str:
    """'PNG, JPG, PDF' — para los mensajes de error, ordenado y sin el punto."""
    return ', '.join(sorted(ext.lstrip('.').upper() for ext in extensions))


def _get_extension(file: Any) -> str:
    """
    Última extensión del nombre, en minúsculas.

    Se toma la última a propósito: 'factura.png.exe' debe resolver a '.exe' y ser rechazado
    por las categorías de imagen, no aceptado por el '.png' intermedio.
    """
    return Path(getattr(file, 'name', '') or '').suffix.lower()


def _verify_image(file: BinaryIO, extension: str) -> None:
    """Comprueba que el contenido sea una imagen realmente decodificable."""
    previous_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    try:
        # verify() no decodifica el bitmap completo: valida la estructura y aborta barato.
        # Deja el objeto inutilizable, por eso no se reutiliza después.
        Image.open(file).verify()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ValidationError({
            'file': [f'El contenido del archivo no es una imagen '
                     f'{extension.lstrip(".").upper()} válida.']
        }) from exc
    finally:
        Image.MAX_IMAGE_PIXELS = previous_limit
        file.seek(0)


def _verify_signature(file: BinaryIO, extension: str) -> None:
    """Comprueba la firma de bytes de los formatos no-imagen."""
    header = file.read(_SNIFF_BYTES)
    file.seek(0)

    if extension in _TEXT_EXTENSIONS:
        try:
            header.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise ValidationError({
                'file': ['El contenido del archivo no es texto plano válido.']
            }) from exc
        return

    signatures = _SIGNATURES.get(extension)
    if signatures and not any(header.startswith(sig) for sig in signatures):
        raise ValidationError({
            'file': [f'El contenido del archivo no corresponde a un '
                     f'{extension.lstrip(".").upper()} válido.']
        })


def _resolve_max_bytes(category: UploadCategory, tenant: Any) -> tuple[int, bool]:
    """
    Tope efectivo en bytes y si proviene del plan.

    El tope del plan nunca puede superar el tope duro de la categoría. `None` en el plan
    significa ilimitado, en cuyo caso rige el tope duro.
    """
    hard_max_bytes = category.hard_max_mb * MB

    if category.plan_key is None:
        fixed_mb = (
            category.fixed_max_mb if category.fixed_max_mb is not None else category.hard_max_mb
        )
        return min(fixed_mb * MB, hard_max_bytes), False

    from utils.plans import get_effective_plan_limits

    plan_mb = get_effective_plan_limits(tenant.plan).get(category.plan_key)
    if plan_mb is None:  # ilimitado en el plan → rige el tope duro
        return hard_max_bytes, False

    plan_bytes = plan_mb * MB
    # Estrictamente menor: si el plan coincide con el tope duro (Enterprise), el rechazo no es
    # "por plan" y no tiene sentido invitar a un upgrade que no existe.
    return min(plan_bytes, hard_max_bytes), plan_bytes < hard_max_bytes


def validate_upload(file: Any, *, category: str, tenant: Any = None) -> None:
    """
    Valida un archivo subido contra la política de su categoría.

    Comprueba, en orden: extensión permitida, tipo real por contenido (nunca el
    `content_type` del cliente, que es falsificable), tope de tamaño y cuota de
    almacenamiento del tenant.

    Args:
        file: UploadedFile de `request.FILES`.
        category: clave de UPLOAD_CATEGORIES.
        tenant: instancia de Tenant. Necesario para los topes por plan y la cuota.

    Raises:
        ValueError: categoría inexistente, o categoría gateada por plan sin `tenant`
            (errores de programación, no del usuario).
        ValidationError: 400 — extensión o contenido no permitido, o supera el tope duro.
        PlanLimitExceeded: 402 — supera el tope del plan o la cuota de almacenamiento.
    """
    try:
        config = UPLOAD_CATEGORIES[category]
    except KeyError:
        raise ValueError(
            f"Categoría de subida desconocida: '{category}'. "
            f'Válidas: {", ".join(sorted(UPLOAD_CATEGORIES))}.'
        ) from None

    # Falla ruidosamente en vez de saltarse el tope del plan en silencio si un call site
    # olvida pasar el tenant.
    if config.plan_key is not None and tenant is None:
        raise ValueError(
            f"La categoría '{category}' se limita por plan: 'tenant' es obligatorio."
        )

    extension = _get_extension(file)
    if extension not in config.extensions:
        shown = extension or 'sin extensión'
        raise ValidationError({
            'file': [f'El tipo de archivo {shown} no está permitido. '
                     f'Formatos aceptados: {_label(config.extensions)}.']
        })

    if extension in _IMAGE_EXTENSIONS:
        _verify_image(file, extension)
    else:
        _verify_signature(file, extension)

    max_bytes, from_plan = _resolve_max_bytes(config, tenant)
    if file.size > max_bytes:
        max_mb = max_bytes // MB
        if from_plan:
            from core.exceptions import PlanLimitExceeded

            raise PlanLimitExceeded(
                detail=f'El archivo supera el límite de {max_mb} MB de tu plan. '
                       f'Cambia a un plan superior para aumentar la capacidad.'
            )
        raise ValidationError({'file': [f'El archivo supera el límite de {max_mb} MB.']})

    if config.counts_toward_storage and tenant is not None:
        from apps.rbac.permissions import check_storage_limit

        check_storage_limit(tenant, file.size)
