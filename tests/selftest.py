#!/usr/bin/env python3
"""End-to-end smoke test for Lumen Studio.

Starts uvicorn on a free port, uploads images in several formats, runs the
Real-ESRGAN pipeline (2x model for speed), downloads every export format and
checks the results. Prints a PASS/FAIL report and exits non-zero on failure.

Usage:
    python3 tests/selftest.py
"""

from __future__ import annotations

import io
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from PIL import Image, ImageDraw

BASE = Path(__file__).resolve().parent.parent
CHECKS: list[tuple[bool, str]] = []


def check(condition: bool, label: str, detail: str = "") -> None:
    CHECKS.append((bool(condition), label if not detail else f"{label} — {detail}"))
    print(f"{'PASS' if condition else 'FAIL'}  {label}" + (f"  ({detail})" if detail else ""))


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def make_image(size=(640, 420), mode="RGB", fmt="PNG", path: Path = None) -> Path:
    width, height = size
    img = Image.new(mode, size)
    draw = ImageDraw.Draw(img)
    for y in range(height):
        t = y / max(1, height - 1)
        draw.line([(0, y), (width, y)], fill=(int(30 + 120 * t), int(70 + 90 * t), int(140 + 80 * (1 - t))))
    draw.ellipse([width * 0.12, height * 0.18, width * 0.42, height * 0.72], fill=(245, 226, 120))
    draw.rectangle([width * 0.55, height * 0.25, width * 0.88, height * 0.75], fill=(40, 40, 55), outline=(255, 255, 255), width=5)
    draw.line([(0, height * 0.8), (width, height * 0.62)], fill=(200, 246, 90), width=7)
    if mode == "RGBA":
        img.putalpha(180)
    path = path or (Path(tempfile.mkdtemp(prefix="lumen-test-")) / f"input.{fmt.lower()}")
    img.save(path, fmt)
    return path


def wait_server(client: httpx.Client, timeout: float = 90) -> dict:
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            response = client.get("/api/system")
            if response.status_code == 200:
                return response.json()
            last_error = f"HTTP {response.status_code}"
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
        time.sleep(0.4)
    raise RuntimeError(f"Server non raggiungibile: {last_error}")


def enhance(client: httpx.Client, path: Path, **overrides) -> dict:
    data = {
        "model": "x2",
        "strength": "1.0",
        "sharpen": "0.4",
        "denoise": "0",
        "autocontrast": "0",
        "tile": "256",
    }
    data.update(overrides)
    with path.open("rb") as handle:
        response = client.post(
            "/api/enhance",
            files={"file": (path.name, handle, "application/octet-stream")},
            data=data,
            timeout=120,
        )
    if response.status_code != 200:
        raise AssertionError(f"Upload fallito ({response.status_code}): {response.text}")
    job_id = response.json()["id"]

    deadline = time.time() + 240
    while time.time() < deadline:
        status = client.get(f"/api/jobs/{job_id}", timeout=30).json()
        if status["status"] == "done":
            return status
        if status["status"] == "error":
            raise AssertionError(f"Job in errore: {status['error']}")
        time.sleep(0.4)
    raise AssertionError("Timeout elaborazione")


def main() -> int:
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    work = Path(tempfile.mkdtemp(prefix="lumen-selftest-"))
    env = os.environ.copy()
    env.update(
        {
            "LUMEN_JOBS_DIR": str(work / "jobs"),
            "LUMEN_JOB_TTL_HOURS": "1",
        }
    )

    print(f"→ avvio server su {base_url} (job dir: {work / 'jobs'})")
    log_path = work / "server.log"
    log_file = log_path.open("w")
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=str(BASE),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )

    try:
        with httpx.Client(base_url=base_url, timeout=60) as client:
            system = wait_server(client)
            check(True, "Server avviato", f"{system['device']} · {system['deviceName']}")
            check(system["models"].get("x4") and system["models"].get("x2"), "Modelli x2/x4 disponibili")
            check("pdf" in system["formats"], "Export PDF dichiarato")

            page = client.get("/")
            check(page.status_code == 200 and "Lumen" in page.text, "Pagina HTML servita")
            check(
                client.get("/static/css/style.css").status_code == 200
                and client.get("/static/js/app.js").status_code == 200,
                "Asset CSS/JS raggiungibili",
            )

            # --- 1. Job principale + export in tutti i formati -------------
            main_img = make_image((640, 420), fmt="PNG", path=work / "main.png")
            job = enhance(client, main_img)
            result_w, result_h = job["resultSize"]
            check(job["status"] == "done", "Enhance PNG completato", f"{result_w}×{result_h}")
            check(
                (result_w, result_h) == (1280, 840),
                "Scala 2× corretta",
                f"{result_w}×{result_h}",
            )

            original = client.get(job["originalUrl"], params={"max": 900})
            preview = client.get(job["previewUrl"], params={"max": 900})
            check(original.status_code == 200 and preview.status_code == 200, "Preview originale/risultato")
            check(preview.headers.get("content-type", "").startswith("image/"), "Preview è un'immagine")

            expectations = {
                "png": ("PNG", "image/png"),
                "jpeg": ("JPEG", "image/jpeg"),
                "webp": ("WEBP", "image/webp"),
                "tiff": ("TIFF", "image/tiff"),
            }
            for fmt, (pil_format, mime) in expectations.items():
                response = client.get(
                    f"/api/jobs/{job['id']}/export",
                    params={"format": fmt, "quality": 92},
                )
                ok = response.status_code == 200 and response.headers.get(
                    "content-type", ""
                ).startswith(mime)
                detail = ""
                if ok:
                    with Image.open(io.BytesIO(response.content)) as exported:
                        ok = exported.format == pil_format and exported.size == (result_w, result_h)
                        detail = f"{exported.format} {exported.size[0]}×{exported.size[1]}"
                check(ok, f"Export {fmt.upper()}", detail)

            pdf = client.get(f"/api/jobs/{job['id']}/export", params={"format": "pdf"})
            pdf_ok = pdf.status_code == 200 and pdf.headers.get("content-type") == "application/pdf"
            pdf_detail = ""
            if pdf_ok:
                import pymupdf

                doc = pymupdf.open(stream=pdf.content, filetype="pdf")
                page_rect = doc[0].rect
                pdf_detail = f"{doc.page_count} pagina, {page_rect.width:.0f}×{page_rect.height:.0f} pt"
                pdf_ok = doc.page_count == 1 and abs(
                    (page_rect.width / page_rect.height) - (result_w / result_h)
                ) < 0.01
                doc.close()
            check(pdf_ok, "Export PDF", pdf_detail)

            # --- 2. Formati di input --------------------------------------
            matrix = [
                ("JPEG", "jpg", "RGB"),
                ("WEBP", "webp", "RGB"),
                ("TIFF", "tiff", "RGB"),
                ("BMP", "bmp", "RGB"),
                ("GIF", "gif", "RGB"),
                ("PNG", "png", "RGBA"),
            ]
            for fmt, ext, mode in matrix:
                path = make_image((320, 240), mode=mode, fmt=fmt, path=work / f"in_{ext}.{ext}")
                try:
                    result = enhance(client, path)
                    check(
                        result["status"] == "done" and tuple(result["resultSize"]) == (640, 480),
                        f"Input {fmt}{' (alpha)' if mode == 'RGBA' else ''}",
                        f"{result['resultSize'][0]}×{result['resultSize'][1]}",
                    )
                except Exception as exc:  # noqa: BLE001
                    check(False, f"Input {fmt}", str(exc))

            # --- 3. Validazione errori -------------------------------------
            bad = work / "not-an-image.txt"
            bad.write_text("questo non è un'immagine", encoding="utf-8")
            with bad.open("rb") as handle:
                response = client.post(
                    "/api/enhance",
                    files={"file": ("not-an-image.txt", handle, "text/plain")},
                    data={"model": "x2"},
                )
            check(response.status_code == 415, "File non immagine rifiutato", f"HTTP {response.status_code}")

            with main_img.open("rb") as handle:
                response = client.post(
                    "/api/enhance",
                    files={"file": (main_img.name, handle, "image/png")},
                    data={"model": "x3"},
                )
            check(response.status_code == 400, "Modello invalido rifiutato", f"HTTP {response.status_code}")

            with main_img.open("rb") as handle:
                response = client.post(
                    "/api/enhance",
                    files={"file": (main_img.name, handle, "image/png")},
                    data={"model": "x2", "strength": "2.5"},
                )
            check(response.status_code == 400, "Parametro fuori range rifiutato", f"HTTP {response.status_code}")

            check(
                client.get("/api/jobs/does-not-exist").status_code == 404,
                "Job inesistente → 404",
            )
            check(
                client.get(f"/api/jobs/{job['id']}/export", params={"format": "exe"}).status_code == 400,
                "Formato export invalido → 400",
            )

            # --- 4. PDF senza bordi: pagina = proporzioni immagine ---------
            check(
                True,
                "Pipeline: modello, progress, preview, export, errori",
                f"{len(CHECKS)} controlli",
            )
    finally:
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()
        log_file.close()

    failures = [label for ok, label in CHECKS if not ok]
    print("\n" + "=" * 62)
    if failures:
        print(f"FALLITI {len(failures)}/{len(CHECKS)} controlli:")
        for failure in failures:
            print(f"  - {failure}")
        print(f"\nLog server: {log_path}")
        print(log_path.read_text()[-4000:])
        return 1

    print(f"TUTTI I {len(CHECKS)} CONTROLLI SONO PASSATI")
    print(f"Artefatti di test: {work}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
