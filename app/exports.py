"""Lumen Studio - export encoders (PNG / JPEG / WebP / TIFF / PDF)."""

from __future__ import annotations

import io
import re
import unicodedata
from pathlib import Path

from PIL import Image

from .config import EXPORT_FORMATS


def safe_stem(name: str) -> str:
    """Turn an arbitrary upload name into a safe file stem."""
    stem = Path(name).stem
    stem = unicodedata.normalize("NFC", stem)
    stem = re.sub(r"[\\/:*?\"<>|]+", "-", stem).strip(" .")
    stem = re.sub(r"\s+", " ", stem)
    return stem[:80] or "immagine"


def export_bytes(
    master: Path,
    fmt: str,
    quality: int,
    base_name: str,
) -> tuple[bytes, str, str]:
    """Encode the lossless master to the requested format.

    Returns (bytes, filename, mime).
    """
    if fmt not in EXPORT_FORMATS:
        raise ValueError(f"Formato non supportato: {fmt}")

    quality = max(50, min(100, int(quality)))
    spec = EXPORT_FORMATS[fmt]
    filename = f"{safe_stem(base_name)}_enhanced{spec['ext']}"

    buf = io.BytesIO()
    with Image.open(master) as img:
        img = img.convert("RGB")
        if fmt == "png":
            img.save(buf, "PNG", optimize=True)
        elif fmt == "jpeg":
            img.save(buf, "JPEG", quality=quality, subsampling=0, optimize=True)
        elif fmt == "webp":
            img.save(buf, "WEBP", quality=quality, method=6)
        elif fmt == "tiff":
            img.save(buf, "TIFF", compression="tiff_lzw")
        elif fmt == "pdf":
            # Borderless page: same aspect/size as the image at 300 DPI.
            img.save(buf, "PDF", resolution=300.0, quality=quality)

    return buf.getvalue(), filename, spec["mime"]
