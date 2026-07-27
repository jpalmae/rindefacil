"""Normalización de archivos subidos: detecta el tipo real por magic bytes
y ajusta la extensión del filename si no coincide con el contenido.

Esto evita el bug donde un PDF se guarda con extensión .png (porque el
agente IA o el cliente mandaron mal el filename) y después el navegador
no puede renderizarlo.
"""

import os

# Magic bytes soportados para comprobantes.
# La detección se hace por los primeros bytes del archivo (no por extensión).


def _sniff_extension(head: bytes) -> str | None:
    """Inspecta los primeros bytes y devuelve la extensión detectada o None."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return ".gif"
    if head.startswith(b"%PDF"):
        return ".pdf"
    if head.startswith(b"BM"):
        return ".bmp"
    # WEBP: "RIFF" + 4 bytes + "WEBP"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    if head.lstrip().startswith(b"<?xml"):
        return ".svg"
    return None


def detect_extension(file_storage) -> str | None:
    """Lee los primeros bytes del FileStorage SIN consumirlo y devuelve la
    extensión detectada por contenido.

    Werkzeug FileStorage soporta seek()/read() como un file-like.
    """
    try:
        head = file_storage.read(16)
    except Exception:
        return None
    finally:
        try:
            file_storage.seek(0)
        except Exception:
            pass
    return _sniff_extension(head)


def normalize_filename(filename: str, detected_ext: str | None = None) -> str:
    """Ajusta la extensión del filename según el tipo detectado.

    Si detected_ext es None, no se pudo detectar el tipo y se confía en la
    extensión original (fallback). Si hay detección y difiere, se reemplaza.
    """
    if not filename:
        filename = "receipt"
    if detected_ext is None:
        return filename

    current_ext = os.path.splitext(filename)[1].lower()
    detected_ext = detected_ext.lower()
    if current_ext == detected_ext:
        return filename
    # JPEG normaliza a .jpg por consistencia.
    if detected_ext == ".jpg" and current_ext == ".jpeg":
        return filename
    # Caso especial: si detectamos PDF pero el nombre dice .png/.jpg, corregir.
    # Aplica también a cualquier otro mismatch claro.
    base = os.path.splitext(filename)[0]
    return f"{base}{detected_ext}"
