// static/js/ui/cards.js
// -----------------------------------------------------------------------------
// Tarjetas LIVE: construcción/actualización + zoneline.
// -----------------------------------------------------------------------------
// Depende de:
//  - static/js/ui/shared.js  (nick, zona, %)
//  - static/js/core/canvas.js
//  - static/js/config.js     (ZONELINE_*)
// -----------------------------------------------------------------------------

import { CONFIG } from "../config.js";
import { clearCanvas } from "../core/canvas.js";
import {
  getZoneColors,
  zoneClass,
  pctFrom,
  formatNickText,
  fitNick,
  placeNick,
  requestIdle
} from "./shared.js";

// -----------------------------------------------------------------------------
// Construcción de una tarjeta LIVE
// -----------------------------------------------------------------------------
export function buildCard(d, { sessionActive = false } = {}) {
  const user  = d.user || {};
  const apodo = user.apodo || `ID ${d.dev}`;
  const m     = d.metrics || {};

  const hr    = (typeof d.hr === "number") ? d.hr : null;
  const hrmax = m.hr_max ?? null;
  const pct   = pctFrom(hr, hrmax);
  const zone  = m.zone || "Z1";
  const kcal  = (typeof m.kcal === "number") ? m.kcal : null;
  const pts   = (typeof m.points === "number") ? m.points : null;

  // card
  const card = document.createElement("div");
  card.className = `card ${zoneClass(zone)}`;

  // zoneline (canvas)
  const zc = document.createElement("canvas");
  zc.className = "zonebar hidden";
  zc.dataset.dev = d.dev;
  card.appendChild(zc);

  // nick
  const nickEl = document.createElement("div");
  nickEl.className = "nick";
  nickEl.textContent = formatNickText(apodo, d.dev);

  // % central
  const pctEl = document.createElement("div");
  pctEl.className = "pct";
  pctEl.textContent = (pct == null ? "--%" : `${pct}%`);

  // métrica HR
  const hrWrap = document.createElement("div");
  hrWrap.className = "metric hr";
  hrWrap.innerHTML = `
    <span id="hr-${d.dev}">${hr == null ? "--" : hr}</span>
    <span class="icon">
      <svg viewBox="0 0 24 24">
        <path d="M12 21s-5.052-3.247-8.106-6.3C1.84 12.646 1 10.97 1 9.2 1 6.88 2.88 5 5.2 5c1.36 0 2.656.56 3.6 1.56L12 9.04l3.2-2.48C16.144 5.56 17.44 5 18.8 5 21.12 5 23 6.88 23 9.2c0 1.77-.84 3.446-2.894 5.5C17.052 17.753 12 21 12 21z"/>
      </svg>
    </span>
  `;

  // métrica KCAL
  const kWrap = document.createElement("div");
  kWrap.className = "metric kcal";
  kWrap.innerHTML = `
    <span id="kcal-${d.dev}">${kcal == null ? "--" : Math.round(kcal)}</span>
    <span class="icon">
      <svg viewBox="0 0 24 24">
        <path d="M12 2C9.243 5.026 8 7.91 8 10.5A4.5 4.5 0 0 0 12.5 15c2.4 0 4.5-2 4.5-4.5 0-2.59-1.243-5.474-4-8.5zM12 22c5.523 0 10-4.477 10-10 0-4.004-2.383-7.738-6-9.334.666 1.944 1 3.994 1 6.334a6 6 0 1 1-12 0c0-2.34.334-4.39 1-6.334C4.383 4.262 2 7.996 2 12c0 5.523 4.477 10 10 10z"/>
      </svg>
    </span>
  `;

  // métrica puntos / moov
  const mWrap = document.createElement("div");
  mWrap.className = "metric moov";
  mWrap.innerHTML = `
    <span id="moov-${d.dev}">${pts == null ? "--" : Math.round(pts)}</span>
    <span class="icon">
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <circle cx="12" cy="12" r="10"></circle>
        <path d="M7 16V8h2.6l2.4 4 2.4-4H17v8h-2V11.7l-2 3.3h-2L9 11.7V16H7z" fill="var(--bg)"></path>
      </svg>
    </span>
  `;

  card.append(nickEl, pctEl, hrWrap, kWrap, mWrap);

  // ajuste visual después de pintar
  requestIdle(() => {
    fitNick(nickEl);
    placeNick(nickEl);
  });

  // si hay sesión activa, pintamos la zoneline ya
  if (sessionActive) drawZoneBarForDev(d.dev);

  return card;
}

// -----------------------------------------------------------------------------
// Actualización de una tarjeta LIVE
// -----------------------------------------------------------------------------
export function updateCardContent(card, d) {
  const m = d.metrics || {};
  const zone = m.zone || "Z1";

  // solo cambia la clase de zona
  card.className = `card ${zoneClass(zone)}`;

  // nick
  const user  = d.user || {};
  const apodo = user.apodo || `ID ${d.dev}`;
  const nickEl = card.querySelector(".nick");
  if (nickEl) {
    const txt = formatNickText(apodo, d.dev);
    if (nickEl.textContent !== txt) {
      nickEl.textContent = txt;
    }
    requestIdle(() => {
      fitNick(nickEl);
      placeNick(nickEl);
    });
  }

  // HR + %
  const hr   = (typeof d.hr === "number") ? d.hr : null;
  const pct  = pctFrom(hr, m.hr_max ?? null);

  const pctEl = card.querySelector(".pct");
  if (pctEl) pctEl.textContent = (pct == null ? "--%" : `${pct}%`);

  const hrEl = card.querySelector(`#hr-${d.dev}`);
  if (hrEl) hrEl.textContent = (hr == null ? "--" : `${hr}`);

  // kcal / points
  const kcalEl = card.querySelector(`#kcal-${d.dev}`);
  if (kcalEl) kcalEl.textContent = (typeof m.kcal === "number") ? Math.round(m.kcal) : "--";

  const ptsEl = card.querySelector(`#moov-${d.dev}`);
  if (ptsEl) ptsEl.textContent = (typeof m.points === "number") ? Math.round(m.points) : "--";
}

// -----------------------------------------------------------------------------
// Zoneline (canvas superior)
// -----------------------------------------------------------------------------
export async function drawZoneBarForDev(dev) {
  const canvas = document.querySelector(`canvas.zonebar[data-dev="${CSS.escape(dev)}"]`);
  if (!canvas) return;

  const bucketMs = CONFIG.ZONELINE_BUCKET_MS || 5000;
  const windowMs = CONFIG.ZONELINE_WINDOW_MS || 3600000;

  try {
    const url = `/live/zone_timeline?dev=${encodeURIComponent(dev)}&bucket_ms=${bucketMs}&window_ms=${windowMs}`;
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) {
      clearCanvas(canvas);
      canvas.classList.add("hidden");
      return;
    }

    const data = await res.json();
    const ok = renderZoneBar(canvas, data);
    if (!ok) {
      clearCanvas(canvas);
      canvas.classList.add("hidden");
    } else {
      canvas.classList.remove("hidden");
    }
  } catch {
    clearCanvas(canvas);
    canvas.classList.add("hidden");
  }
}

export function refreshAllZoneBars() {
  document.querySelectorAll("canvas.zonebar").forEach((cnv) => {
    const dev = cnv.dataset.dev;
    if (dev) drawZoneBarForDev(dev);
  });
}

function renderZoneBar(canvas, payload) {
  if (!payload || !Array.isArray(payload.timeline) || !payload.timeline.length) {
    return false;
  }

  const { bucket_ms, timeline } = payload;
  const dpr = window.devicePixelRatio || 1;
  const widthCSS = canvas.clientWidth || canvas.parentElement?.clientWidth || 300;
  const rootCSS = getComputedStyle(document.documentElement);
  const heightCSS = parseFloat(rootCSS.getPropertyValue("--zonebar-h")) || 14;

  canvas.width = Math.floor(widthCSS * dpr);
  canvas.height = Math.floor(heightCSS * dpr);

  const ctx = canvas.getContext("2d");
  if (!ctx) return false;

  const colors = getZoneColors();
  const firstT = timeline[0].t;
  const lastT = timeline[timeline.length - 1].t + bucket_ms;
  const span = Math.max(bucket_ms, lastT - firstT);

  ctx.clearRect(0, 0, canvas.width, canvas.height);

  let prevRight = 0;
  for (const item of timeline) {
    const t0 = item.t;
    const t1 = t0 + bucket_ms;
    const z  = item.zone_mode || "Z1";

    const x1 = Math.max(0, Math.min(canvas.width, ((t1 - firstT) / span) * canvas.width));
    const segRight = Math.min(canvas.width, Math.ceil(x1));

    if (segRight > prevRight) {
      ctx.fillStyle = colors[z] || colors.Z1;
      ctx.fillRect(prevRight, 0, segRight - prevRight, canvas.height);
    }
    prevRight = segRight;
  }

  return true;
}
