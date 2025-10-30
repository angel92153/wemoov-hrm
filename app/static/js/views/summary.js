// static/js/views/summary.js
// -----------------------------------------------------------------------------
// Vista SUMMARY (modular):
// - Fuerza al backend a volcar el último resumen (poke /live?limit=1)
// - Polling con 204 / 304 / ETag hasta que llega uno NUEVO
// - Si viene vacío, espera y reintenta
// - Reintentos silenciosos en background para pillar uno más reciente
// - Vuelve a LIVE si hay sesión o countdown
// - Vuelve a LIVE tras SUMMARY_MS
// -----------------------------------------------------------------------------

import { CONFIG } from "../config.js";
import { fetchJSON } from "../core/api.js";
import { buildSummaryCard, redrawAllSummaryBars } from "../ui/summary.js";
import { layoutForCount } from "../ui/shared.js";

const grid = document.getElementById("grid");

let statusTimer = null;
let autoBackTimer = null;
let summaryRetryTimer = null;
let lastETag = null;
let currentRunId = null; // id del resumen actual (si lo manda backend)

/* =========================
   UI: placeholder
   ========================= */
function showHolder(text) {
  grid.innerHTML = "";
  const holder = document.createElement("div");
  holder.className = "card z1";
  holder.style.cssText =
    "display:flex;align-items:center;justify-content:center;height:240px;opacity:1;text-align:center;";
  holder.textContent = text;
  grid.appendChild(holder);
  layoutForCount(1);
  return holder;
}

/* =========================
   "Poke" al backend para forzar generación
   ========================= */
async function pokeBackendToGenerate() {
  try {
    await fetch("/live?limit=1", { cache: "no-store" });
  } catch {
    // silencioso
  }
}

/* =========================
   /live/summary/persisted con ETag
   ========================= */
async function fetchPersisted(etag) {
  const headers = {};
  if (etag) headers["If-None-Match"] = etag;

  const res = await fetch("/live/summary/persisted", {
    headers,
    cache: "no-store",
  });

  if (res.status === 204) {
    const ra = Number(res.headers.get("Retry-After") || "2");
    return { kind: "pending", retryAfter: Number.isFinite(ra) ? ra : 2 };
  }
  if (res.status === 304) {
    return { kind: "not_modified" };
  }
  if (!res.ok) {
    return { kind: "error", message: `HTTP ${res.status}` };
  }

  const data = await res.json().catch(() => null);
  const newETag =
    (res.headers.get("ETag") || "").replaceAll('"', "").trim() || null;

  return { kind: "ok", data, etag: newETag };
}

/* =========================
   Reintento silencioso (no borra tarjetas)
   ========================= */
function scheduleSilentRetry(sec = 4) {
  clearTimeout(summaryRetryTimer);
  summaryRetryTimer = setTimeout(() => {
    loadSummaryWithPolling(true);
  }, Math.max(1, sec) * 1000);
}

/* =========================
   Carga con polling. Si `silent=true`, no muestra "Generando…"
   ========================= */
async function loadSummaryWithPolling(silent = false) {
  const holder = silent ? null : showHolder("Generando resumen…");

  if (!silent) {
    await pokeBackendToGenerate();
  }

  let etag = lastETag;
  const T0 = Date.now();
  const MAX_WAIT = 15000; // 15s

  while (Date.now() - T0 < MAX_WAIT) {
    try {
      const res = await fetchPersisted(etag);

      // 204 → todavía no hay resumen
      if (res.kind === "pending") {
        if (holder) holder.textContent = "Generando resumen…";
        await waitMs((res.retryAfter || 2) * 1000);
        continue;
      }

      // 304 → mismo resumen anterior
      if (res.kind === "not_modified") {
        if (holder) holder.textContent = "Esperando nuevo resumen…";
        await waitMs(2000);
        continue;
      }

      // Error HTTP
      if (res.kind === "error") {
        if (holder) holder.textContent = "Error cargando resumen.";
        scheduleSilentRetry(5);
        return;
      }

      // OK real
      if (res.kind === "ok") {
        const payload = res.data;
        const runId = payload?.run_id || payload?.id || null;

        // ok pero vacío → reintenta
        if (!payload || !Array.isArray(payload.devices) || !payload.devices.length) {
          if (holder) holder.textContent = "Resumen vacío. Esperando datos…";
          await waitMs(2000);
          continue;
        }

        // si ya hemos pintado este run → no repintar
        if (currentRunId && runId && runId === currentRunId) {
          scheduleSilentRetry(5);
          return;
        }

        // pintar
        lastETag = res.etag || null;
        renderSummary(payload, runId);

        // reintento suave para pillar uno más nuevo
        scheduleSilentRetry(5);
        return;
      }

      await waitMs(2000);
    } catch {
      await waitMs(2000);
    }
  }

  // Timeout → mensaje y retry
  if (holder) holder.textContent = "Resumen no disponible aún…";
  scheduleSilentRetry(5);
}

function waitMs(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

/* =========================
   Render resumen
   ========================= */
function renderSummary(payload, runId = null) {
  grid.innerHTML = "";

  const bucket =
    typeof payload.bucket_ms === "number"
      ? payload.bucket_ms
      : CONFIG.SUMMARY_BUCKET_MS || 5000;

  const devices = [...payload.devices].sort((a, b) =>
    String(a.dev).localeCompare(String(b.dev))
  );

  for (const devRow of devices) {
    const card = buildSummaryCard(
      devRow.dev,
      (devRow.user && devRow.user.apodo) || `ID ${devRow.dev}`,
      devRow.metrics || {},
      Array.isArray(devRow.timeline) ? devRow.timeline : [],
      bucket
    );
    grid.appendChild(card);
  }

  layoutForCount(devices.length);
  redrawAllSummaryBars();
  currentRunId = runId;
}

/* =========================
   Auto-volver a LIVE
   ========================= */
async function pollStatusAndReturnIfNeeded() {
  if (document.hidden) return;
  const s = await fetchJSON("/control/status");
  if (!s) return;
  if (s.active || s.show_countdown) {
    window.location.replace("/screen/live");
  }
}

function armAutoBackTimeout() {
  const ms = Number(CONFIG.SUMMARY_MS || 600000); // fallback 10 min
  clearTimeout(autoBackTimer);
  autoBackTimer = setTimeout(() => {
    window.location.replace("/screen/live");
  }, Math.max(0, ms));
}

/* =========================
   Lifecycle
   ========================= */
function onVisible() {
  if (!document.hidden) {
    redrawAllSummaryBars();
    pollStatusAndReturnIfNeeded();
  }
}

function startStatusPolling() {
  clearInterval(statusTimer);
  statusTimer = setInterval(() => {
    if (!document.hidden) pollStatusAndReturnIfNeeded();
  }, 1000);
}

function stopTimers() {
  clearInterval(statusTimer);
  clearTimeout(autoBackTimer);
  clearTimeout(summaryRetryTimer);
}

window.addEventListener("resize", redrawAllSummaryBars);
document.addEventListener("visibilitychange", onVisible);
window.addEventListener("beforeunload", stopTimers);

// === Init ===
armAutoBackTimeout();
startStatusPolling();
loadSummaryWithPolling(false);
