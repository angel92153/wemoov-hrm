// static/js/views/live.js
// -----------------------------------------------------------------------------
// Vista LIVE: gestiona estado de sesión, SSE y fallback a polling.
// - Pinta/actualiza tarjetas y zonelines.
// - Sincroniza la topbar (incluye countdown, sin texto en .phase).
// - Al finalizar la sesión, navega automáticamente a /screen/summary.
// - Cancela redirecciones si detecta nueva sesión o countdown.
// - Optimizado para +16 tarjetas y pausas en segundo plano.
// - 🔄 Fade-out REAL (progresivo) de tarjetas desconectadas.
// -----------------------------------------------------------------------------

import { CONFIG } from "../config.js";
import { fetchJSON, sse } from "../core/api.js";
import {
  buildCard,
  updateCardContent,
  drawZoneBarForDev,
  refreshAllZoneBars,
} from "../ui/cards.js";
import { layoutForCount } from "../ui/shared.js";

const grid = document.getElementById("grid");

// Estado global
let SESSION_ACTIVE = false;
// dev -> { el: HTMLElement, fadeStart: number|null }
let cards = new Map();
let es = null;
let nextTimer = null;
let statusTimer = null;
let _redirectScheduled = false;
let _resizeRAF = 0;
let _layoutLock = false;

/* =========================
   Utilidades básicas
   ========================= */
function gotoSummarySoon(delayMs = 0) {
  if (_redirectScheduled) return;
  _redirectScheduled = true;
  setTimeout(() => window.location.replace("/screen/summary"), Math.max(0, delayMs));
}

function cancelPendingRedirect() {
  _redirectScheduled = false;
}

function safeLayout() {
  if (_layoutLock) return;
  _layoutLock = true;
  requestAnimationFrame(() => {
    layoutForCount(cards.size);
    refreshAllZoneBars();
    _layoutLock = false;
  });
}

/* =========================
   Sesión / Topbar
   ========================= */
async function pollStatus() {
  const s = await fetchJSON("/control/status");
  if (!s) {
    Topbar.clearPhase();
    SESSION_ACTIVE = false;
    return;
  }

  const wasActive = SESSION_ACTIVE;
  SESSION_ACTIVE = !!s.active;

  // ⏳ Countdown previo
  if (!SESSION_ACTIVE && s.show_countdown) {
    cancelPendingRedirect();
    Topbar.startPhaseTimer({
      seconds: s.countdown_s ?? 0,
      key: "",
      color: s.phase_color || "#ffffff",
    });
    return;
  }

  if (SESSION_ACTIVE) {
    cancelPendingRedirect();
    Topbar.startPhaseTimer({
      seconds: s.phase_remaining_s ?? 0,
      key: s.phase_key || "Sesión",
      color: s.phase_color || null,
    });
  } else {
    Topbar.clearPhase();
    if (wasActive && !_redirectScheduled) gotoSummarySoon(200);
  }
}

/* =========================
   Reconciliar tarjetas (con fade JS)
   ========================= */
function reconcile(list) {
  const arr = Array.isArray(list) ? list : [];
  const nowDevs = new Set(arr.map((d) => d.dev));

  const now = Date.now();
  const fadeMs = CONFIG.FADE_DURATION_MS || 60000; // 60s por defecto

  // 1) Altas / updates
  for (const d of arr) {
    let entry = cards.get(d.dev);
    if (!entry) {
      const el = buildCard(d, { sessionActive: SESSION_ACTIVE });
      cards.set(d.dev, { el, fadeStart: null });
      grid.appendChild(el);
    } else {
      updateCardContent(entry.el, d);
      // si había empezado a desvanecerse pero ha vuelto a emitir, lo recuperamos
      if (entry.fadeStart !== null) {
        entry.fadeStart = null;
        entry.el.style.opacity = "1";
        entry.el.style.transform = ""; // por si acaso
      }
    }
  }

  // 2) Bajas → fade progresivo
  for (const [dev, entry] of cards) {
    if (!nowDevs.has(dev)) {
      if (entry.fadeStart === null) {
        // empezar a contar
        entry.fadeStart = now;
      } else {
        const elapsed = now - entry.fadeStart;
        if (elapsed >= fadeMs) {
          // borrar del DOM
          try {
            grid.removeChild(entry.el);
          } catch {}
          cards.delete(dev);
        } else {
          // opacidad progresiva: de 1 → 0
          const p = Math.min(1, elapsed / fadeMs);
          const alpha = 1 - p;
          entry.el.style.opacity = String(alpha);
          // un poquito de scale para que se note
          entry.el.style.transform = `scale(${1 - p * 0.02})`;
        }
      }
    }
  }

  // 3) Layout + zonelines
  safeLayout();
  if (SESSION_ACTIVE) arr.forEach((d) => drawZoneBarForDev(d.dev));
}

/* =========================
   SSE con fallback
   ========================= */
async function startStream() {
  es = sse(
    "/live/stream",
    (data) => {
      try {
        reconcile(JSON.parse(data));
      } catch {
        /* ignore */
      }
    },
    () => {
      es = null;
      tick();
    }
  );

  if (!es) {
    // no SSE → polling
    tick();
  } else {
    // escuchar eventos de estado
    es.addEventListener("status", (e) => {
      try {
        const s = JSON.parse(e.data);
        const wasActive = SESSION_ACTIVE;
        const active = !!(s && s.active);

        if (!active && s?.show_countdown) {
          cancelPendingRedirect();
          Topbar.startPhaseTimer({
            seconds: s.countdown_s ?? 0,
            key: "",
            color: s.phase_color || "#ffffff",
          });
          SESSION_ACTIVE = false;
          return;
        }

        if (active) {
          cancelPendingRedirect();
          Topbar.startPhaseTimer({
            seconds: s.phase_remaining_s ?? 0,
            key: s.phase_key || "Sesión",
            color: s.phase_color || null,
          });
          SESSION_ACTIVE = true;
        } else {
          Topbar.clearPhase();
          SESSION_ACTIVE = false;
          if (wasActive && !_redirectScheduled) gotoSummarySoon(200);
        }
      } catch {
        /* ignore */
      }
    });
  }
}

/* =========================
   Polling fallback
   ========================= */
async function tick() {
  const list = await fetchJSON("/live?limit=32", { timeout: 8000 });
  reconcile(list || []);
  clearTimeout(nextTimer);
  nextTimer = setTimeout(tick, list && list.length ? 1000 : 5000);
}

/* =========================
   Lifecycle
   ========================= */
function onVisible() {
  if (!document.hidden) {
    safeLayout();
    pollStatus();
  }
}

function onResize() {
  if (_resizeRAF) cancelAnimationFrame(_resizeRAF);
  _resizeRAF = requestAnimationFrame(() => {
    safeLayout();
    _resizeRAF = 0;
  });
}

function cleanup() {
  try {
    if (es) es.close();
  } catch {}
  es = null;
  clearTimeout(nextTimer);
  clearInterval(statusTimer);
  window.removeEventListener("resize", onResize);
  document.removeEventListener("visibilitychange", onVisible);
  window.removeEventListener("beforeunload", cleanup);
}

async function start() {
  await pollStatus();
  await startStream();

  document.addEventListener("visibilitychange", onVisible);
  window.addEventListener("resize", onResize);

  // Poll de estado de respaldo
  clearInterval(statusTimer);
  statusTimer = setInterval(() => {
    if (!document.hidden) pollStatus();
  }, 1000);

  window.addEventListener("beforeunload", cleanup);
}

start();
