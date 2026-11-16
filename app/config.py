"""Lumen Studio - configuration."""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
JOBS_DIR = Path(os.environ.get("LUMEN_JOBS_DIR", BASE_DIR / "jobs")).expanduser()

# Models: env override -> models/ next to the app.
_MODEL_CANDIDATES = []
if os.environ.get("LUMEN_MODELS_DIR"):
    _MODEL_CANDIDATES.append(Path(os.environ["LUMEN_MODELS_DIR"]).expanduser())
_MODEL_CANDIDATES.append(BASE_DIR / "models")


def models_dir() -> Path:
    for candidate in _MODEL_CANDIDATES:
        if (candidate / "RealESRGAN_x4plus.pth").is_file():
            return candidate
    return _MODEL_CANDIDATES[0]


MAX_UPLOAD_MB = int(os.environ.get("LUMEN_MAX_UPLOAD_MB", "40"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
MAX_INPUT_PIXELS = int(os.environ.get("LUMEN_MAX_INPUT_PIXELS", "30000000"))
MAX_OUTPUT_PIXELS = int(os.environ.get("LUMEN_MAX_OUTPUT_PIXELS", "60000000"))
JOB_TTL_SECONDS = int(os.environ.get("LUMEN_JOB_TTL_HOURS", "12")) * 3600

EXPORT_FORMATS = {
    "png": {"label": "PNG", "ext": ".png", "mime": "image/png"},
    "jpeg": {"label": "JPEG", "ext": ".jpg", "mime": "image/jpeg"},
    "webp": {"label": "WebP", "ext": ".webp", "mime": "image/webp"},
    "tiff": {"label": "TIFF", "ext": ".tif", "mime": "image/tiff"},
    "pdf": {"label": "PDF", "ext": ".pdf", "mime": "application/pdf"},
}

# Pillow formats offered by the file picker / accepted by the backend.
INPUT_EXTENSIONS = [
    "png", "jpg", "jpeg", "webp", "tif", "tiff", "bmp", "gif", "ppm", "pgm",
]
