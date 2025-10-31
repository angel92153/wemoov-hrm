// static/js/ui/cards.js
// -----------------------------------------------------------------------------
// Tarjetas LIVE: construcción/actualización + zoneline.
// -----------------------------------------------------------------------------
// Depende de:
//  - static/js/ui/shared.js  (nick, zona, %, iconos)
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
  requestIdle,
  makeHeartIcon,
  makeFlameIcon,
  makeMoovIcon,
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
  const hrSpan = document.createElement("span");
  hrSpan.id = `hr-${d.dev}`;
  hrSpan.textContent = (hr == null ? "--" : hr);
  hrWrap.append(hrSpan, makeHeartIcon());

  // métrica KCAL
  const kWrap = document.createElement("div");
  kWrap.className = "metric kcal";
  const kcalSpan = document.createElement("span");
  kcalSpan.id = `kcal-${d.dev}`;
  kcalSpan.textContent = (kcal == null ? "--" : Math.round(kcal));
  kWrap.append(kcalSpan, makeFlameIcon());

  // métrica puntos / moov
  const mWrap = document.createElement("div");
  mWrap.className = "metric moov";
  const ptsSpan = document.createElement("span");
  ptsSpan.id = `moov-${d.dev}`;
  ptsSpan.textContent = (pts == null ? "--" : Math.round(pts));
  mWrap.append(ptsSpan, makeMoovIcon());

  // ensamblar la tarjeta
  card.append(nickEl, pctEl, hrWrap, kWrap, mWrap);

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

  // actualizar clase de zona
  card.className = `card ${zoneClass(zone)}`;

  // nick
  const user  = d.user || {};
  const apodo = user.apodo || `ID ${d.dev}`;
  const nickEl = card.querySelector(".nick");
  if (nickEl) {
    const txt = formatNickText(apodo, d.dev);
    if (nickEl.textContent !== txt) {
      nickEl.textContent = txt;
      // ❌ sin fitNick/placeNick aquí; lo hace layoutForCount
    }
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
  if (kcalEl)
    kcalEl.textContent = (typeof m.kcal === "number") ? Math.round(m.kcal) : "--";

  const ptsEl = card.querySelector(`#moov-${d.dev}`);
  if (ptsEl)
    ptsEl.textContent = (typeof m.points === "number") ? Math.round(m.points) : "--";
}

// -----------------------------------------------------------------------------
// Zoneline (canvas superior)
// -----------------------------------------------------------------------------
export async function drawZoneBarForDev(dev) {
  const canvas = document.querySelector(
    `canvas.zonebar[data-dev="${CSS.escape(dev)}"]`
  );
  if (!canvas) return;

  const bucketMs = CONFIG.ZONELINE_BUCKET_MS || 5000;
  const windowMs = CONFIG.ZONELINE_WINDOW_MS || 3600000;

  try {
    const url = `/live/zone_timeline?dev=${encodeURIComponent(
      dev
    )}&bucket_ms=${bucketMs}&window_ms=${windowMs}`;
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
  const widthCSS =
    canvas.clientWidth || canvas.parentElement?.clientWidth || 300;
  const rootCSS = getComputedStyle(document.documentElement);
  const heightCSS =
    parseFloat(rootCSS.getPropertyValue("--zonebar-h")) || 14;

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
    const z = item.zone_mode || "Z1";

    const x1 = Math.max(
      0,
      Math.min(canvas.width, ((t1 - firstT) / span) * canvas.width)
    );
    const segRight = Math.min(canvas.width, Math.ceil(x1));

    if (segRight > prevRight) {
      ctx.fillStyle = colors[z] || colors.Z1;
      ctx.fillRect(prevRight, 0, segRight - prevRight, canvas.height);
    }
    prevRight = segRight;
  }

  // 🔽🔽🔽 AÑADIR ESTO: línea negra inferior dentro del canvas
  ctx.fillStyle = "#000";
  // 1px en unidades de canvas (ajustado a dpr)
  const lineH = 1 * dpr;
  ctx.fillRect(0, canvas.height - lineH, canvas.width, lineH);

  return true;
}

