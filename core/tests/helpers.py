"""
Helpers compartidos entre tests de varias apps.

`png_bytes` existe porque el validador de subidas (utils/uploads.py) comprueba el
contenido real con Pillow: los fixtures históricos del tipo b'\\x89PNG fake' ya no
pasan, y con razón — nunca debieron aceptarse.
"""
import io

from PIL import Image


def png_bytes(size: tuple[int, int] = (1, 1)) -> bytes:
    """PNG real y decodificable, del tamaño en píxeles indicado."""
    buffer = io.BytesIO()
    Image.new('RGB', size).save(buffer, format='PNG')
    return buffer.getvalue()
