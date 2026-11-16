"""Lumen Studio - FastAPI application."""

from __future__ import annotations

import io
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image

from . import config
from .exports import export_bytes
from .jobs import JobStore
from .pipeline import NATIVE_SCALE, EnhanceOptions, Enhancer

MODELS_DIR = config.models_dir()
enhancer = Enhancer(MODELS_DIR)
store = JobStore(config.JOBS_DIR, enhancer, config.JOB_TTL_SECONDS)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    print(
        f"[lumen] device={enhancer.device.type} ({enhancer.device_name}) "
        f"models={MODELS_DIR}",
        file=sys.stderr,
    )
    yield


app = FastAPI(
    title="Lumen Studio",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)

SUFFIX_BY_FORMAT = {
    "JPEG": ".jpg",
    "MPO": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
    "TIFF": ".tif",
    "BMP": ".bmp",
    "GIF": ".gif",
    "PPM": ".ppm",
    "PGM": ".pgm",
}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _number(value: str, name: str, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"Valore non valido per {name}.")
    if not minimum <= parsed <= maximum:
        raise HTTPException(
            status_code=400,
            detail=f"{name} deve essere tra {minimum} e {maximum}.",
        )
    return parsed


def _read_upload(data: bytes) -> tuple[str, tuple[int, int]]:
    if not data:
        raise HTTPException(status_code=400, detail="File vuoto.")
    try:
        probe = Image.open(io.BytesIO(data))
        probe.verify()
        image = Image.open(io.BytesIO(data))
        fmt = (image.format or "").upper()
        size = image.size
        image.close()
        probe.close()
    except Exception:
        raise HTTPException(
            status_code=415,
            detail="Formato non riconosciuto. Carica un'immagine PNG, JPEG, WebP, TIFF, BMP o GIF.",
        )
    return fmt, size


def _job_or_404(job_id: str):
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Lavoro non trovato o scaduto.")
    return job


def _content_disposition(filename: str) -> str:
    ascii_name = (
        filename.encode("ascii", "ignore").decode("ascii").strip() or "immagine"
    )
    return (
        f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(filename)}"
    )


# --------------------------------------------------------------------------- #
# Pages / info
# --------------------------------------------------------------------------- #
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(config.STATIC_DIR / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


@app.get("/api/system")
def system_info():
    return {
        "device": enhancer.device.type,
        "deviceName": enhancer.device_name,
        "models": enhancer.available_models(),
        "maxUploadMB": config.MAX_UPLOAD_MB,
        "maxOutputMegapixels": round(config.MAX_OUTPUT_PIXELS / 1_000_000, 1),
        "formats": list(config.EXPORT_FORMATS),
        "inputExtensions": config.INPUT_EXTENSIONS,
        "jobTtlHours": config.JOB_TTL_SECONDS // 3600,
    }


# --------------------------------------------------------------------------- #
# Enhancement
# --------------------------------------------------------------------------- #
@app.post("/api/enhance")
async def enhance(
    file: UploadFile = File(...),
    model: str = Form("x4"),
    scale: str = Form("auto"),
    strength: float = Form(1.0),
    sharpen: float = Form(0.4),
    denoise: float = Form(0.0),
    autocontrast: float = Form(0.0),
    tile: int = Form(256),
):
    if model not in NATIVE_SCALE:
        raise HTTPException(status_code=400, detail="Modello non valido.")
    if not enhancer.available_models().get(model):
        raise HTTPException(status_code=503, detail=f"Modello {model} non disponibile.")

    _number(str(strength), "strength", 0.0, 1.0)
    _number(str(sharpen), "sharpen", 0.0, 3.0)
    _number(str(denoise), "denoise", 0.0, 5.0)
    _number(str(autocontrast), "autocontrast", 0.0, 30.0)
    if tile < 0 or tile > 2048:
        raise HTTPException(status_code=400, detail="Dimensione tile non valida.")

    target_scale: float | None
    if scale in ("auto", ""):
        target_scale = None
    else:
        target_scale = _number(scale, "scale", 0.5, 8.0)
        if abs(target_scale - round(target_scale)) < 1e-6:
            target_scale = float(round(target_scale))

    data = bytearray()
    while chunk := await file.read(1024 * 1024):
        data.extend(chunk)
        if len(data) > config.MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"File troppo grande: il limite è {config.MAX_UPLOAD_MB} MB.",
            )

    fmt, size = _read_upload(bytes(data))
    width, height = size
    if width * height > config.MAX_INPUT_PIXELS:
        raise HTTPException(
            status_code=413,
            detail=(
                "Immagine troppo grande: "
                f"massimo {config.MAX_INPUT_PIXELS / 1_000_000:.0f} megapixel."
            ),
        )

    effective_scale = target_scale or NATIVE_SCALE[model]
    output_px = width * height * effective_scale * effective_scale
    if output_px > config.MAX_OUTPUT_PIXELS:
        raise HTTPException(
            status_code=413,
            detail=(
                "Il risultato sarebbe troppo grande per la memoria disponibile "
                f"({output_px / 1_000_000:.0f} MP). Scegli il modello 2× o usa "
                "un'immagine più piccola."
            ),
        )

    suffix = SUFFIX_BY_FORMAT.get(fmt)
    if not suffix:
        suffix = Path(file.filename or "image").suffix.lower()
        if not suffix or len(suffix) > 6:
            suffix = ".img"

    options = EnhanceOptions(
        model=model,
        scale=target_scale,
        strength=strength,
        sharpen=sharpen,
        denoise=denoise,
        autocontrast=autocontrast,
        tile=max(0, int(tile)),
    )
    job = store.create(
        bytes(data),
        suffix,
        file.filename or f"immagine{suffix}",
        options,
        size,
    )
    return {"id": job.id}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    return _job_or_404(job_id).to_dict()


@app.get("/api/jobs/{job_id}/original")
def job_original(
    job_id: str,
    max: int = Query(1800, ge=200, le=4000),
):
    job = _job_or_404(job_id)
    return FileResponse(
        store.preview_path(job, "original", max),
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@app.get("/api/jobs/{job_id}/preview")
def job_preview(
    job_id: str,
    max: int = Query(1800, ge=200, le=4000),
):
    job = _job_or_404(job_id)
    if job.status != "done":
        raise HTTPException(status_code=409, detail="Elaborazione non completata.")
    return FileResponse(
        store.preview_path(job, "result", max),
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@app.get("/api/jobs/{job_id}/export")
def job_export(
    job_id: str,
    format: str = Query("png"),
    quality: int = Query(95, ge=50, le=100),
):
    job = _job_or_404(job_id)
    if job.status != "done":
        raise HTTPException(status_code=409, detail="Elaborazione non completata.")
    if format not in config.EXPORT_FORMATS:
        raise HTTPException(status_code=400, detail="Formato di export non supportato.")

    try:
        payload, filename, mime = export_bytes(
            job.result_path, format, quality, job.original_name
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Export non riuscito: {exc}")

    return Response(
        content=payload,
        media_type=mime,
        headers={
            "Content-Disposition": _content_disposition(filename),
            "Content-Length": str(len(payload)),
            "Cache-Control": "no-store",
        },
    )


app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
