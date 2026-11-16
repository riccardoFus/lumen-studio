"""Lumen Studio - enhancement pipeline on top of Real-ESRGAN / spandrel.

Adapted from the `enhance.py` CLI so the web app can report progress and reuse
a single in-memory model instance between jobs.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import torch
from PIL import Image, ImageFilter, ImageOps

try:
    from spandrel import ModelLoader
except ImportError:  # pragma: no cover - handled in main.py
    ModelLoader = None

ProgressFn = Callable[[float, str], None]

MODEL_FILES = {
    "x4": "RealESRGAN_x4plus.pth",
    "x2": "RealESRGAN_x2plus.pth",
}
NATIVE_SCALE = {"x4": 4.0, "x2": 2.0}


@dataclass
class EnhanceOptions:
    model: str = "x4"
    scale: float | None = None
    strength: float = 1.0
    sharpen: float = 0.4
    autocontrast: float = 0.0
    denoise: float = 0.0
    tile: int = 256
    pad: int = 16


def warm_image(path: Path) -> Image.Image:
    """Open, rotate by EXIF and flatten transparency on white."""
    img = ImageOps.exif_transpose(Image.open(path))
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
        background = Image.new("RGB", img.size, "white")
        background.paste(img, mask=img.getchannel("A"))
        return background
    return img.convert("RGB")


class Enhancer:
    """Loads models lazily and serialises access to the GPU/CPU."""

    def __init__(self, models_dir: Path):
        self.models_dir = Path(models_dir)
        self.device = torch.device(
            "cuda" if torch.cuda.is_available() else "cpu"
        )
        self.half = self.device.type == "cuda"
        self._models: dict[str, object] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ info
    @property
    def device_name(self) -> str:
        if self.device.type == "cuda":
            try:
                return torch.cuda.get_device_name(0)
            except Exception:
                return "CUDA"
        return "CPU"

    def available_models(self) -> dict[str, bool]:
        return {
            name: (self.models_dir / filename).is_file()
            for name, filename in MODEL_FILES.items()
        }

    # ----------------------------------------------------------------- model
    def _load(self, name: str):
        if name in self._models:
            return self._models[name]

        path = self.models_dir / MODEL_FILES[name]
        if not path.is_file():
            raise FileNotFoundError(f"Modello non trovato: {path}")
        if ModelLoader is None:
            raise RuntimeError("spandrel non installato: pip install spandrel")

        descriptor = ModelLoader().load_from_file(str(path))
        descriptor.model.eval()
        descriptor.to(self.device)
        if self.half:
            descriptor.model.half()
        self._models[name] = descriptor
        return descriptor

    # ------------------------------------------------------------- inference
    @torch.no_grad()
    def _infer(self, model, tensor: torch.Tensor) -> torch.Tensor:
        return model(tensor)

    def _run_model(
        self,
        descriptor,
        img: Image.Image,
        tile: int,
        pad: int,
        progress: ProgressFn,
    ) -> Image.Image:
        arr = np.asarray(img, dtype=np.float32) / 255.0
        tensor = torch.from_numpy(arr).permute(2, 0, 1).unsqueeze(0).to(self.device)
        if next(descriptor.model.parameters()).dtype == torch.float16:
            tensor = tensor.half()

        scale = float(descriptor.scale)

        if tile <= 0 or (img.width <= tile and img.height <= tile):
            progress(0.01, "Ricostruzione dettagli…")
            out = self._infer(descriptor.model, tensor)
            progress(1.0, "Ricostruzione completata")
            return _tensor_to_image(out)

        _, _, h, w = tensor.shape
        out_h, out_w = int(h * scale), int(w * scale)
        acc = torch.zeros((1, 3, out_h, out_w), dtype=tensor.dtype, device=self.device)
        weight = torch.zeros((1, 1, out_h, out_w), dtype=tensor.dtype, device=self.device)

        xs = list(range(0, w, tile))
        ys = list(range(0, h, tile))
        total = len(xs) * len(ys)
        done = 0

        for y in ys:
            for x in xs:
                x0, y0 = max(0, x - pad), max(0, y - pad)
                x1, y1 = min(w, x + tile + pad), min(h, y + tile + pad)
                patch = tensor[:, :, y0:y1, x0:x1]
                patch_out = self._infer(descriptor.model, patch)

                ox0, oy0 = int(x0 * scale), int(y0 * scale)
                ox1, oy1 = int(x1 * scale), int(y1 * scale)
                acc[:, :, oy0:oy1, ox0:ox1] += patch_out
                weight[:, :, oy0:oy1, ox0:ox1] += 1.0

                done += 1
                progress(
                    done / total,
                    f"Ricostruzione dettagli… {done}/{total} riquadri",
                )

        acc = acc / weight.clamp(min=1.0)
        return _tensor_to_image(acc)

    # -------------------------------------------------------------- pipeline
    def enhance(
        self,
        img: Image.Image,
        options: EnhanceOptions,
        progress: ProgressFn | None = None,
    ) -> Image.Image:
        progress = progress or (lambda _p, _s: None)
        model_name = options.model if options.model in MODEL_FILES else "x4"

        with self._lock:
            progress(0.02, "Caricamento del modello AI…")
            descriptor = self._load(model_name)
            progress(0.10, "Modello pronto")

            def inference_progress(fraction: float, stage: str) -> None:
                progress(0.10 + 0.80 * fraction, stage)

            ai = self._run_model(
                descriptor, img, options.tile, options.pad, inference_progress
            )

            progress(0.92, "Rifinitura dei dettagli…")
            if options.strength < 1.0:
                base = img.resize(ai.size, Image.LANCZOS)
                if options.strength <= 0.0:
                    ai = base
                else:
                    ai = Image.blend(base, ai, options.strength)

            target_scale = options.scale or float(descriptor.scale)
            if abs(target_scale - descriptor.scale) > 1e-6:
                target = (
                    max(1, round(img.width * target_scale)),
                    max(1, round(img.height * target_scale)),
                )
                ai = ai.resize(target, Image.LANCZOS)

            if options.denoise > 0:
                ai = ai.filter(ImageFilter.GaussianBlur(radius=options.denoise))
            if options.autocontrast > 0:
                ai = ImageOps.autocontrast(ai, cutoff=options.autocontrast)
            if options.sharpen > 0:
                ai = ai.filter(
                    ImageFilter.UnsharpMask(
                        radius=2.0, percent=int(options.sharpen * 100), threshold=3
                    )
                )

            progress(1.0, "Completato")
            return ai


def _tensor_to_image(tensor: torch.Tensor) -> Image.Image:
    tensor = tensor.squeeze(0).float().clamp(0, 1).cpu()
    arr = (tensor.permute(1, 2, 0).numpy() * 255.0).round().astype(np.uint8)
    return Image.fromarray(arr, "RGB")
