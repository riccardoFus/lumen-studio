/* Lumen Studio — frontend logic */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const els = {
    hero: $("hero"),
    workspace: $("workspace"),
    exportPanel: $("exportPanel"),
    dropzone: $("dropzone"),
    fileInput: $("fileInput"),
    dropFormats: $("dropFormats"),
    deviceLabel: $("deviceLabel"),
    deviceChip: $("deviceChip"),
    footerDevice: $("footerDevice"),
    fileThumb: $("fileThumb"),
    fileName: $("fileName"),
    fileMeta: $("fileMeta"),
    modelGroup: $("modelGroup"),
    modelHint: $("modelHint"),
    presetChips: $("presetChips"),
    strength: $("strength"),
    strengthOut: $("strengthOut"),
    sharpen: $("sharpen"),
    sharpenOut: $("sharpenOut"),
    denoise: $("denoise"),
    denoiseOut: $("denoiseOut"),
    enhanceBtn: $("enhanceBtn"),
    enhanceLabel: $("enhanceLabel"),
    statusText: $("statusText"),
    resetBtn: $("resetBtn"),
    stageTitle: $("stageTitle"),
    stageSub: $("stageSub"),
    stageIdle: $("stageIdle"),
    sourcePreview: $("sourcePreview"),
    compare: $("compare"),
    beforeImg: $("beforeImg"),
    afterImg: $("afterImg"),
    beforeWrap: $("beforeWrap"),
    compareLine: $("compareLine"),
    compareRange: $("compareRange"),
    processing: $("processing"),
    progressOrb: $("progressOrb"),
    progressNum: $("progressNum"),
    progressStage: $("progressStage"),
    resultBadge: $("resultBadge"),
    stageHint: $("stageHint"),
    stageMeta: $("stageMeta"),
    formatGroup: $("formatGroup"),
    qualityBlock: $("qualityBlock"),
    quality: $("quality"),
    qualityOut: $("qualityOut"),
    downloadBtn: $("downloadBtn"),
    downloadLabel: $("downloadLabel"),
    downloadExt: $("downloadExt"),
    toast: $("toast"),
  };

  const PRESETS = {
    balanced: { model: "x4", strength: 100, sharpen: 0.4, denoise: 0 },
    natural: { model: "x2", strength: 90, sharpen: 0.2, denoise: 0 },
    detail: { model: "x4", strength: 100, sharpen: 0.7, denoise: 0 },
    clean: { model: "x4", strength: 85, sharpen: 0.25, denoise: 0.4 },
  };

  const FORMAT_EXT = { png: ".png", jpeg: ".jpg", webp: ".webp", tiff: ".tif", pdf: ".pdf" };
  const FORMAT_META = {
    png: { mime: "image/png", label: "Immagine PNG" },
    jpeg: { mime: "image/jpeg", label: "Immagine JPEG" },
    webp: { mime: "image/webp", label: "Immagine WebP" },
    tiff: { mime: "image/tiff", label: "Immagine TIFF" },
    pdf: { mime: "application/pdf", label: "Documento PDF" },
  };
  const LOSSLESS = new Set(["png", "tiff"]);

  const state = {
    file: null,
    objectUrl: null,
    previewOk: false,
    width: 0,
    height: 0,
    jobId: null,
    job: null,
    pollTimer: null,
    pollFails: 0,
    busy: false,
    format: "png",
    system: null,
    run: 0,
  };

  let toastTimer = null;

  // ------------------------------------------------------------------ utils
  function showToast(message, isError = false) {
    els.toast.textContent = message;
    els.toast.classList.toggle("error", isError);
    els.toast.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => els.toast.classList.remove("show"), 4200);
  }

  function formatBytes(bytes) {
    if (!bytes) return "—";
    const units = ["B", "KB", "MB", "GB"];
    let value = bytes;
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) {
      value /= 1024;
      unit += 1;
    }
    return `${value.toFixed(value >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`;
  }

  function updateTrack(input) {
    const min = Number(input.min);
    const max = Number(input.max);
    const pct = max > min ? ((Number(input.value) - min) / (max - min)) * 100 : 0;
    input.style.setProperty("--p", pct.toFixed(1));
  }

  function syncOutputs() {
    els.strengthOut.textContent = `${els.strength.value}%`;
    els.sharpenOut.textContent = Number(els.sharpen.value).toFixed(2);
    els.denoiseOut.textContent = Number(els.denoise.value).toFixed(2);
    els.qualityOut.textContent = els.quality.value;
    [els.strength, els.sharpen, els.denoise, els.quality].forEach(updateTrack);
  }

  function selectedModel() {
    const active = els.modelGroup.querySelector("button.active");
    return active ? active.dataset.model : "x4";
  }

  function setModel(model) {
    els.modelGroup.querySelectorAll("button").forEach((btn) => {
      const on = btn.dataset.model === model;
      btn.classList.toggle("active", on);
      btn.setAttribute("aria-checked", String(on));
    });
    els.modelHint.textContent = model === "x2" ? "Più fedele all'originale" : "Massimo dettaglio";
  }

  function clearPreset() {
    els.presetChips.querySelectorAll(".chip-btn").forEach((chip) => chip.classList.remove("active"));
  }

  function applyPreset(name) {
    const preset = PRESETS[name];
    if (!preset) return;
    setModel(preset.model);
    els.strength.value = preset.strength;
    els.sharpen.value = preset.sharpen;
    els.denoise.value = preset.denoise;
    syncOutputs();
    els.presetChips.querySelectorAll(".chip-btn").forEach((chip) => {
      chip.classList.toggle("active", chip.dataset.preset === name);
    });
  }

  function startRun() {
    state.run += 1;
    return state.run;
  }

  function stopPolling() {
    if (state.pollTimer) {
      clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  }

  function setBusy(busy) {
    state.busy = busy;
    els.enhanceBtn.disabled = busy;
    els.enhanceLabel.textContent = busy ? "Elaborazione…" : "Migliora immagine";
    els.modelGroup.querySelectorAll("button").forEach((btn) => { btn.disabled = busy; });
    els.presetChips.querySelectorAll(".chip-btn").forEach((chip) => { chip.disabled = busy; });
    [els.strength, els.sharpen, els.denoise].forEach((input) => { input.disabled = busy; });
  }

  // ---------------------------------------------------------------- system
  async function loadSystem() {
    try {
      const res = await fetch("/api/system");
      if (!res.ok) return;
      state.system = await res.json();
      const name = state.system.deviceName || "CPU";
      els.deviceChip.title = name;
      els.deviceLabel.textContent = state.system.device === "cuda" ? "GPU" : "CPU";
      els.footerDevice.textContent = `${name} · elaborazione locale`;
      if (Array.isArray(state.system.inputExtensions)) {
        const shown = state.system.inputExtensions
          .filter((ext) => !["ppm", "pgm"].includes(ext))
          .slice(0, 6)
          .map((ext) => ext.toUpperCase().replace("JPEG", "JPG"));
        els.dropFormats.textContent = shown.join(" · ");
      }
    } catch (_) {
      // offline-safe: il badge resta sul default
    }
  }

  // ------------------------------------------------------------------ file
  function clearJob() {
    stopPolling();
    state.run += 1;
    state.jobId = null;
    state.job = null;
    state.pollFails = 0;
    state.busy = false;
    els.exportPanel.classList.add("hidden");
  }

  function clearSelection() {
    clearJob();
    if (state.objectUrl) URL.revokeObjectURL(state.objectUrl);
    state.objectUrl = null;
    state.file = null;
    state.previewOk = false;
    state.width = 0;
    state.height = 0;
  }

  function resetAll() {
    clearSelection();
    els.workspace.classList.add("hidden");
    els.exportPanel.classList.add("hidden");
    els.hero.classList.remove("hidden");
    els.sourcePreview.classList.add("hidden");
    els.sourcePreview.removeAttribute("src");
    els.compare.classList.add("hidden");
    els.processing.classList.add("hidden");
    els.stageIdle.classList.remove("hidden");
    els.stageTitle.textContent = "Anteprima";
    els.stageSub.textContent = "Carica un'immagine per iniziare";
    els.stageHint.textContent = "Trascina il cursore per confrontare.";
    els.stageMeta.textContent = "";
    els.resultBadge.classList.add("hidden");
    els.statusText.textContent = "Pronto quando vuoi.";
    els.statusText.classList.remove("error");
    els.fileThumb.style.backgroundImage = "";
    setBusy(false);
    els.stageHint.textContent = "Trascina il cursore per confrontare.";
    applyPreset("balanced");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function pickFile(file) {
    if (!file) return;
    const limitMb = state.system?.maxUploadMB ?? 40;
    const looksImage =
      (file.type && file.type.startsWith("image/")) ||
      /\.(png|jpe?g|webp|tiff?|bmp|gif|ppm|pgm|avif|heic|heif)$/i.test(file.name);
    if (!looksImage) {
      showToast("Formato non supportato: scegli un file immagine.", true);
      return;
    }
    if (file.size > limitMb * 1024 * 1024) {
      showToast(`File troppo grande: il limite è ${limitMb} MB.`, true);
      return;
    }

    clearSelection();
    state.file = file;
    state.objectUrl = URL.createObjectURL(file);
    state.previewOk = false;

    els.fileName.textContent = file.name;
    els.fileMeta.textContent = formatBytes(file.size);
    els.fileThumb.style.backgroundImage = `url("${state.objectUrl}")`;

    els.hero.classList.add("hidden");
    els.workspace.classList.remove("hidden");
    els.exportPanel.classList.add("hidden");
    els.compare.classList.add("hidden");
    els.processing.classList.add("hidden");
    els.stageIdle.classList.add("hidden");
    els.stageIdle.querySelector("p").textContent = "La tua immagine apparirà qui";
    els.resultBadge.classList.add("hidden");
    els.statusText.textContent = "Pronto quando vuoi.";
    els.statusText.classList.remove("error");
    els.stageTitle.textContent = "Anteprima originale";
    els.stageSub.textContent = "Caricamento anteprima…";
    els.stageHint.textContent = "Scegli le impostazioni e avvia il miglioramento.";
    els.stageMeta.textContent = "";
    els.sourcePreview.src = state.objectUrl;
    els.sourcePreview.classList.remove("hidden");

    const probe = new Image();
    probe.onload = () => {
      state.width = probe.naturalWidth;
      state.height = probe.naturalHeight;
      state.previewOk = true;
      const dims = `${state.width} × ${state.height} px`;
      els.fileMeta.textContent = `${dims} · ${formatBytes(file.size)}`;
      els.stageSub.textContent = dims;

      // Con 4× un'immagine oltre ~4 MP supererebbe il limite di memoria:
      // passa automaticamente al modello 2× e avvisa l'utente.
      const maxOutput = (state.system?.maxOutputMegapixels ?? 60) * 1_000_000;
      if (state.width * state.height * 16 > maxOutput) {
        setModel("x2");
        clearPreset();
        els.statusText.textContent =
          "Immagine grande: uso il modello 2× per restare nei limiti di memoria.";
      }
    };
    probe.onerror = () => {
      state.previewOk = false;
      els.sourcePreview.classList.add("hidden");
      els.stageIdle.classList.remove("hidden");
      els.stageIdle.querySelector("p").textContent =
        "Anteprima non disponibile per questo formato — verrà comunque elaborata.";
      els.stageSub.textContent = formatBytes(file.size);
    };
    probe.src = state.objectUrl;

    setTimeout(() => {
      els.workspace.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 60);
  }

  // -------------------------------------------------------------- enhance
  function showProcessing(progress, stage) {
    els.stageHint.textContent = "Elaborazione in corso…";
    els.stageMeta.textContent = "";
    els.stageIdle.classList.add("hidden");
    els.sourcePreview.classList.add("hidden");
    els.compare.classList.add("hidden");
    els.processing.classList.remove("hidden");
    updateProgress(progress, stage);
  }

  function updateProgress(progress, stage) {
    const pct = Math.max(0, Math.min(100, Math.round(progress)));
    els.progressOrb.style.setProperty("--p", String(pct));
    els.progressNum.textContent = `${pct}%`;
    if (stage) els.progressStage.textContent = stage;
  }

  async function startEnhance() {
    if (!state.file || state.busy) return;
    clearJob();
    const run = startRun();
    setBusy(true);
    els.statusText.classList.remove("error");
    els.statusText.textContent = "Invio dell'immagine…";
    showProcessing(0, "Preparazione…");
    els.stageTitle.textContent = "Elaborazione";
    els.stageSub.textContent = "Il modello sta ricostruendo i dettagli";

    const body = new FormData();
    body.append("file", state.file, state.file.name);
    body.append("model", selectedModel());
    body.append("strength", (Number(els.strength.value) / 100).toFixed(2));
    body.append("sharpen", Number(els.sharpen.value).toFixed(2));
    body.append("denoise", Number(els.denoise.value).toFixed(2));
    body.append("tile", "256");

    try {
      const res = await fetch("/api/enhance", { method: "POST", body });
      if (!res.ok) {
        const payload = await res.json().catch(() => ({}));
        throw new Error(payload.detail || `Errore del server (${res.status})`);
      }
      const data = await res.json();
      if (run !== state.run) return;
      state.jobId = data.id;
      state.pollFails = 0;
      stopPolling();
      state.pollTimer = setInterval(() => pollJob(run), 650);
      pollJob(run);
    } catch (error) {
      if (run !== state.run) return;
      failEnhance(error.message);
    }
  }

  async function pollJob(run) {
    if (!state.jobId || run !== state.run) return;
    try {
      const res = await fetch(`/api/jobs/${state.jobId}`);
      if (!res.ok) throw new Error("Lavoro non trovato");
      const job = await res.json();
      if (run !== state.run) return;
      state.job = job;
      state.pollFails = 0;

      if (job.status === "done") {
        stopPolling();
        finishEnhance(job);
        return;
      }
      if (job.status === "error") {
        stopPolling();
        failEnhance(job.error || "Elaborazione non riuscita.");
        return;
      }
      updateProgress((job.progress || 0) * 100, job.stage || "Elaborazione…");
      els.statusText.textContent = job.stage || "Elaborazione…";
    } catch (error) {
      state.pollFails += 1;
      if (state.pollFails >= 6) {
        stopPolling();
        failEnhance("Connessione persa con il server.");
      }
    }
  }

  function finishEnhance(job) {
    setBusy(false);
    const modelTag = (job.options?.model || "x4").replace("x", "") + "×";
    els.processing.classList.add("hidden");
    els.compare.classList.remove("hidden");
    els.stageTitle.textContent = "Prima / dopo";
    const [w, h] = job.resultSize || [0, 0];
    const elapsed = job.elapsed ? ` · ${job.elapsed}s` : "";
    els.stageSub.textContent = `${w} × ${h} px${elapsed}`;
    els.stageMeta.textContent = `${w} × ${h} px · ${modelTag}`;
    els.stageHint.textContent = "Trascina il cursore per confrontare originale e risultato.";
    els.resultBadge.textContent = `${modelTag} · ${w} × ${h}`;
    els.resultBadge.classList.remove("hidden");
    els.statusText.textContent = "Miglioramento completato.";
    els.statusText.classList.remove("error");

    const ratio = w && h ? w / h : 1.5;
    els.compare.style.aspectRatio = String(ratio);
    setCompare(50);

    els.afterImg.onload = () => setCompare(Number(els.compareRange.value));
    els.afterImg.src = `${job.previewUrl}?max=1800&v=${Date.now()}`;
    els.beforeImg.src = state.previewOk
      ? state.objectUrl
      : `${job.originalUrl}?max=1800&v=${Date.now()}`;

    updateDownloadLink();
    els.exportPanel.classList.remove("hidden");
    setTimeout(() => {
      els.exportPanel.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }, 250);
  }

  function failEnhance(message) {
    setBusy(false);
    state.jobId = null;
    els.processing.classList.add("hidden");
    els.sourcePreview.classList.toggle("hidden", !state.previewOk);
    els.stageIdle.classList.toggle("hidden", state.previewOk);
    els.stageTitle.textContent = "Anteprima originale";
    els.stageHint.textContent = "Scegli le impostazioni e avvia il miglioramento.";
    els.statusText.textContent = message;
    els.statusText.classList.add("error");
    showToast(message, true);
  }

  // --------------------------------------------------------------- compare
  function setCompare(position) {
    const pct = Math.max(0, Math.min(100, position));
    els.beforeWrap.style.clipPath = `inset(0 ${100 - pct}% 0 0)`;
    els.compareLine.style.left = `${pct}%`;
    els.compareRange.value = String(pct);
    els.compareRange.setAttribute("aria-valuetext", `${Math.round(pct)}% originale`);
  }

  // ---------------------------------------------------------------- export
  function setFormat(format) {
    state.format = format;
    els.formatGroup.querySelectorAll(".format").forEach((btn) => {
      const on = btn.dataset.format === format;
      btn.classList.toggle("active", on);
      btn.setAttribute("aria-checked", String(on));
    });
    els.downloadExt.textContent = FORMAT_EXT[format] || ".png";
    els.qualityBlock.classList.toggle("hidden", LOSSLESS.has(format));
    updateDownloadLink();
  }

  function suggestedFilename() {
    const raw = state.file?.name || "immagine";
    const stem = raw
      .replace(/\.[^.]+$/, "")
      .replace(/[\\/:*?"<>|]+/g, "-")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 80) || "immagine";
    return `${stem}_enhanced${FORMAT_EXT[state.format] || ".png"}`;
  }

  function setDownloadBusy(busy) {
    els.downloadBtn.classList.toggle("busy", busy);
    els.downloadLabel.textContent = busy ? "Preparo…" : "Scarica";
    if (busy) {
      els.downloadBtn.setAttribute("aria-disabled", "true");
    } else if (state.jobId) {
      els.downloadBtn.removeAttribute("aria-disabled");
    }
  }

  async function saveResult() {
    if (!state.jobId) {
      showToast("Migliora prima un'immagine.", true);
      return;
    }
    const format = state.format;
    const meta = FORMAT_META[format];
    const filename = suggestedFilename();
    const url =
      `/api/jobs/${state.jobId}/export?format=${format}` +
      `&quality=${els.quality.value}`;

    // 1) Finestra di salvataggio nativa (Chrome/Edge). Va aperta subito,
    //    mentre il click dell'utente è ancora un'azione valida.
    let handle = null;
    if (typeof window.showSaveFilePicker === "function") {
      try {
        const options = { suggestedName: filename };
        // Chrome valida i MIME accettati: per TIFF lasciamo il filtro libero.
        if (format !== "tiff") {
          options.types = [
            {
              description: meta.label,
              accept: { [meta.mime]: [FORMAT_EXT[format]] },
            },
          ];
        }
        handle = await window.showSaveFilePicker(options);
      } catch (error) {
        if (error && error.name === "AbortError") return; // annullato dall'utente
        handle = null; // API non disponibile in questo contesto → fallback
      }
    }

    setDownloadBusy(true);
    try {
      const response = await fetch(url);
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || `Errore del server (${response.status})`);
      }
      const blob = await response.blob();

      if (handle) {
        const writable = await handle.createWritable();
        await writable.write(blob);
        await writable.close();
        showToast(`Salvato: ${filename}`);
      } else {
        // Fallback (Firefox/Safari): download classico del browser.
        const objectUrl = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = objectUrl;
        anchor.download = filename;
        anchor.rel = "noopener";
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
        showToast("Download avviato.");
      }
    } catch (error) {
      showToast(`Salvataggio non riuscito: ${error.message}`, true);
    } finally {
      setDownloadBusy(false);
    }
  }

  function updateDownloadLink() {
    if (!state.jobId) {
      els.downloadBtn.setAttribute("href", "#");
      els.downloadBtn.setAttribute("aria-disabled", "true");
      return;
    }
    els.downloadBtn.removeAttribute("aria-disabled");
    els.downloadBtn.href =
      `/api/jobs/${state.jobId}/export?format=${state.format}` +
      `&quality=${els.quality.value}`;
  }

  // --------------------------------------------------------------- events
  els.dropzone.addEventListener("click", () => els.fileInput.click());
  els.dropzone.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      els.fileInput.click();
    }
  });
  els.fileInput.addEventListener("change", () => {
    const file = els.fileInput.files && els.fileInput.files[0];
    els.fileInput.value = "";
    pickFile(file);
  });

  ["dragenter", "dragover"].forEach((name) => {
    els.dropzone.addEventListener(name, (event) => {
      event.preventDefault();
      event.stopPropagation();
      els.dropzone.classList.add("dragover");
    });
  });
  ["dragleave", "dragend", "drop"].forEach((name) => {
    els.dropzone.addEventListener(name, (event) => {
      event.preventDefault();
      event.stopPropagation();
      els.dropzone.classList.remove("dragover");
    });
  });
  els.dropzone.addEventListener("drop", (event) => {
    const file = event.dataTransfer?.files?.[0];
    pickFile(file);
  });
  window.addEventListener("dragover", (event) => event.preventDefault());
  window.addEventListener("drop", (event) => event.preventDefault());

  els.modelGroup.querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", () => {
      setModel(btn.dataset.model);
      clearPreset();
    });
  });

  els.presetChips.querySelectorAll(".chip-btn").forEach((chip) => {
    chip.addEventListener("click", () => applyPreset(chip.dataset.preset));
  });

  [els.strength, els.sharpen, els.denoise, els.quality].forEach((input) => {
    input.addEventListener("input", () => {
      if (input !== els.quality) clearPreset();
      syncOutputs();
      if (input === els.quality) updateDownloadLink();
    });
  });

  els.enhanceBtn.addEventListener("click", startEnhance);
  els.resetBtn.addEventListener("click", resetAll);

  // Drag su tutta l'area dell'immagine, non solo sul cursore invisibile.
  let compareDragging = false;

  function moveCompareFromClientX(clientX) {
    const rect = els.compare.getBoundingClientRect();
    if (!rect.width) return;
    setCompare(((clientX - rect.left) / rect.width) * 100);
  }

  els.compare.addEventListener("pointerdown", (event) => {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    compareDragging = true;
    els.compare.classList.add("dragging");
    if (els.compare.setPointerCapture) {
      try {
        els.compare.setPointerCapture(event.pointerId);
      } catch (_) {
        /* no-op */
      }
    }
    moveCompareFromClientX(event.clientX);
    event.preventDefault();
  });

  els.compare.addEventListener("pointermove", (event) => {
    if (!compareDragging) return;
    moveCompareFromClientX(event.clientX);
  });

  function endCompareDrag(event) {
    if (!compareDragging) return;
    compareDragging = false;
    els.compare.classList.remove("dragging");
    if (els.compare.hasPointerCapture && els.compare.hasPointerCapture(event.pointerId)) {
      els.compare.releasePointerCapture(event.pointerId);
    }
  }
  els.compare.addEventListener("pointerup", endCompareDrag);
  els.compare.addEventListener("pointercancel", endCompareDrag);

  els.compareRange.addEventListener("input", () => setCompare(Number(els.compareRange.value)));

  els.formatGroup.querySelectorAll(".format").forEach((btn) => {
    btn.addEventListener("click", () => setFormat(btn.dataset.format));
  });

  els.downloadBtn.addEventListener("click", (event) => {
    event.preventDefault();
    if (!els.downloadBtn.classList.contains("busy")) saveResult();
  });

  window.addEventListener("beforeunload", () => {
    if (state.objectUrl) URL.revokeObjectURL(state.objectUrl);
    stopPolling();
  });

  // ------------------------------------------------------------------ init
  syncOutputs();
  setFormat("png");
  loadSystem();
})();
