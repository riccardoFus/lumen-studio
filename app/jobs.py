"""Lumen Studio - job queue and on-disk job storage."""

from __future__ import annotations

import json
import shutil
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import torch
from PIL import Image

from .pipeline import EnhanceOptions, Enhancer, warm_image


@dataclass
class Job:
    id: str
    directory: Path
    original_name: str
    input_path: Path
    options: EnhanceOptions
    status: str = "queued"
    progress: float = 0.0
    stage: str = "In coda"
    error: str | None = None
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    source_size: tuple[int, int] = (0, 0)
    result_size: tuple[int, int] = (0, 0)

    @property
    def result_path(self) -> Path:
        return self.directory / "result.png"

    def to_dict(self) -> dict:
        elapsed = None
        if self.started:
            elapsed = round((self.finished or time.time()) - self.started, 1)
        return {
            "id": self.id,
            "status": self.status,
            "progress": round(self.progress, 4),
            "stage": self.stage,
            "error": self.error,
            "originalName": self.original_name,
            "sourceSize": list(self.source_size),
            "resultSize": list(self.result_size),
            "elapsed": elapsed,
            "options": {
                "model": self.options.model,
                "scale": self.options.scale,
                "strength": self.options.strength,
                "sharpen": self.options.sharpen,
                "denoise": self.options.denoise,
                "autocontrast": self.options.autocontrast,
            },
            "previewUrl": f"/api/jobs/{self.id}/preview" if self.status == "done" else None,
            "originalUrl": f"/api/jobs/{self.id}/original",
        }


class JobStore:
    def __init__(self, root: Path, enhancer: Enhancer, ttl_seconds: int):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.enhancer = enhancer
        self.ttl = ttl_seconds
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lumen")
        self.cleanup(force=True)

    # ------------------------------------------------------------------ api
    def create(
        self,
        data: bytes,
        suffix: str,
        original_name: str,
        options: EnhanceOptions,
        source_size: tuple[int, int],
    ) -> Job:
        self.cleanup()
        job_id = uuid.uuid4().hex
        directory = self.root / job_id
        directory.mkdir(parents=True, exist_ok=True)
        input_path = directory / f"input{suffix}"
        input_path.write_bytes(data)

        job = Job(
            id=job_id,
            directory=directory,
            original_name=original_name,
            input_path=input_path,
            options=options,
            source_size=source_size,
        )
        with self._lock:
            self._jobs[job_id] = job
        self._executor.submit(self._run, job_id)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cleanup(self, force: bool = False) -> None:
        now = time.time()
        for path in self.root.iterdir():
            if not path.is_dir():
                continue
            try:
                age = now - path.stat().st_mtime
            except OSError:
                continue
            if force or age > self.ttl:
                shutil.rmtree(path, ignore_errors=True)
                with self._lock:
                    self._jobs.pop(path.name, None)

    # --------------------------------------------------------------- worker
    def _update(self, job: Job, progress: float, stage: str) -> None:
        with self._lock:
            job.progress = max(0.0, min(1.0, progress))
            job.stage = stage

    def _run(self, job_id: str) -> None:
        job = self.get(job_id)
        if job is None:
            return
        with self._lock:
            job.status = "processing"
            job.started = time.time()
            job.progress = 0.01
            job.stage = "Apertura immagine…"
        try:
            img = warm_image(job.input_path)
            with self._lock:
                job.source_size = img.size

            result = self.enhancer.enhance(
                img,
                job.options,
                progress=lambda p, s: self._update(job, p, s),
            )

            self._update(job, 0.98, "Salvataggio del risultato…")
            result.save(job.result_path, "PNG", compress_level=6)

            with self._lock:
                job.result_size = result.size
                job.status = "done"
                job.progress = 1.0
                job.stage = "Pronto"
                job.finished = time.time()
            self._write_meta(job)
        except torch.cuda.OutOfMemoryError:
            self._fail(
                job,
                "Memoria GPU esaurita: prova il modello 2× o un'immagine più piccola.",
            )
            traceback.print_exc()
        except Exception as exc:  # noqa: BLE001
            self._fail(job, _friendly_error(exc))
            traceback.print_exc()

    def _fail(self, job: Job, message: str) -> None:
        with self._lock:
            job.status = "error"
            job.error = message
            job.stage = "Errore"
            job.finished = time.time()
        self._write_meta(job)

    def _write_meta(self, job: Job) -> None:
        try:
            (job.directory / "meta.json").write_text(
                json.dumps(job.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    # -------------------------------------------------------------- preview
    def preview_path(self, job: Job, kind: str, max_side: int) -> Path:
        source = job.input_path if kind == "original" else job.result_path
        target = job.directory / f"preview_{kind}_{max_side}.jpg"
        if not target.is_file():
            img = warm_image(source)
            img.thumbnail((max_side, max_side), Image.LANCZOS)
            tmp = target.with_suffix(".tmp")
            img.save(tmp, "JPEG", quality=88, optimize=True)
            tmp.replace(target)
        return target


def _friendly_error(exc: Exception) -> str:
    text = str(exc) or exc.__class__.__name__
    if isinstance(exc, FileNotFoundError):
        return text
    if "out of memory" in text.lower():
        return "Memoria esaurita: prova il modello 2× o un'immagine più piccola."
    if isinstance(exc, Image.UnidentifiedImageError):
        return "Il file non sembra un'immagine supportata."
    return f"Elaborazione non riuscita: {text}"
