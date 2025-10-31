// static/js/ui/summary.js
// -----------------------------------------------------------------------------
// Construcción de tarjetas de SUMMARY + colocación y render de la relief bar.
// Se apoya en el diseño modular (core.css + layout.css).
// -----------------------------------------------------------------------------

import { CONFIG } from "../config.js";
import {
  formatNickText,
  fitNick,
  placeNick,
  getZoneColors,
  requestIdle,
  makeHeartIcon,
  makeFlameIcon,
  makeMoovIcon,
} from "./shared.js";

const summaryBars = new Map(); // dev -> canvas

// ============================================================================
// buildSummaryCard
// ============================================================================
export function buildSummaryCard(dev, apodo, metrics, timeline, bucket_ms) {
  const card = document.createElement("div");
  card.className = "card summary"; 

  // Nick
  const nickEl = document.createElement("div");
  nickEl.className = "nick";
  nickEl.textContent = formatNickText(apodo, dev);

  // % central en resumen lo dejamos vacío
  const pctEl = document.createElement("div");
  pctEl.className = "pct";
  pctEl.textContent = "";

  // Métricas inferiores:
  // - izquierda: % medio (si viene) → lo pintamos como número
  const hrWrap = document.createElement("div");
  hrWrap.className = "metric hr";
  const hrSpan = document.createElement("span");
  hrSpan.id = `hr-${dev}`;
  const pctAvg = Number(metrics?.pct_avg ?? NaN);
  hrSpan.textContent = Number.isFinite(pctAvg) ? `${Math.round(pctAvg)}%` : "--";
  hrWrap.append(hrSpan, makeHeartIcon());

  // - centro: kcal
  const kWrap = document.createElement("div");
  kWrap.className = "metric kcal";
  const kcalEl = document.createElement("span");
  kcalEl.id = `kcal-${dev}`;
  const kcal = Number(metrics?.kcal ?? NaN);
  kcalEl.textContent = Number.isFinite(kcal) ? `${Math.round(kcal)}` : "--";
  kWrap.append(kcalEl, makeFlameIcon());

  // - derecha: puntos
  const mWrap = document.createElement("div");
  mWrap.className = "metric moov";
  const ptsEl = document.createElement("span");
  ptsEl.id = `moov-${dev}`;
  const pts = Number(metrics?.points ?? NaN);
  ptsEl.textContent = Number.isFinite(pts) ? `${Math.round(pts)}` : "--";
  mWrap.append(ptsEl, makeMoovIcon());

  card.append(nickEl, pctEl, hrWrap, kWrap, mWrap);

  // Canvas de la relief bar
  const relief = document.createElement("canvas");
  relief.className = "summarybar";
  card.appendChild(relief);

  // guardamos el payload para redibujar en resize
  relief._summaryPayload = {
    timeline: Array.isArray(timeline) ? timeline : [],
    bucket_ms: bucket_ms || (CONFIG.SUMMARY_BUCKET_MS || 5000),
  };

  summaryBars.set(dev, relief);

  // colocar y dibujar cuando la card ya tenga tamaño real
  requestIdle(() => {
    fitNick(nickEl);
    placeNick(nickEl);
    positionAndRenderSummaryBar(relief);
  });

  return card;
}

// ============================================================================
// Recolocar todas (en resize / layout)
// ============================================================================
export function redrawAllSummaryBars() {
  for (const [, cnv] of summaryBars.entries()) {
    positionAndRenderSummaryBar(cnv);
  }
}

/// ============================================================================
// Colocar el canvas para ocupar EXACTAMENTE el hueco entre nick y métricas
// ============================================================================
function positionAndRenderSummaryBar(canvas) {
  if (!canvas) return;
  const card = canvas.closest(".card");
  if (!card) return;

  const nickEl = card.querySelector(".nick");
  const metricsRef = card.querySelector(".hr");
  if (!nickEl || !metricsRef) return;

  const topMargin = 8;
  const gapAboveMetrics = 6;

  const top = nickEl.offsetTop + nickEl.offsetHeight + topMargin;
  const bottomY = metricsRef.offsetTop - gapAboveMetrics;
  const availH = Math.max(30, bottomY - top);

  // márgenes laterales desde root
  const rootCss = getComputedStyle(document.documentElement);
  const sideGapVar =
    parseFloat(rootCss.getPropertyValue("--inner-side-gap")) || 0;
  const sidePx = Math.max(sideGapVar, 8);

  canvas.style.top = `${top}px`;
  canvas.style.left = `${sidePx}px`;
  canvas.style.right = `${sidePx}px`;
  canvas.style.height = `${availH}px`;
  canvas.style.bottom = "";

  // pintar en el siguiente frame
  requestAnimationFrame(() => {
    const payload = canvas._summaryPayload || {
      timeline: [],
      bucket_ms: CONFIG.SUMMARY_BUCKET_MS || 5000,
    };
    renderReliefBar(canvas, payload.timeline, payload.bucket_ms);
  });
}

function renderReliefBar(canvas, timeline, bucket_ms) {
  if (!Array.isArray(timeline) || timeline.length === 0) {
    const ctx = canvas.getContext("2d");
    if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
    return;
  }

  const card = canvas.closest(".card");
  const dpr = window.devicePixelRatio || 1;

  // ancho útil = ancho de la card - left - right que hemos puesto arriba
  const cardRect = card.getBoundingClientRect();
  const leftPx = parseFloat(canvas.style.left || "0") || 0;
  const rightPx = parseFloat(canvas.style.right || "0") || 0;
  const widthCSS = Math.max(1, cardRect.width - leftPx - rightPx);

  let heightCSS = canvas.getBoundingClientRect().height;
  if (!heightCSS || heightCSS <= 0) heightCSS = 120;

  canvas.width = Math.floor(widthCSS * dpr);
  canvas.height = Math.floor(heightCSS * dpr);

  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  const colors = getZoneColors();
  const totalBuckets = timeline.length;
  const bucketWidth = canvas.width / totalBuckets;

  for (let i = 0; i < totalBuckets; i++) {
    const item = timeline[i];
    const x0 = Math.round(i * bucketWidth);
    const w = Math.ceil(bucketWidth);

    const frac = Math.max(0, Math.min(1, Number(item.frac) || 0));
    const h = Math.round(frac * canvas.height);
    const y = canvas.height - h;

    const z = item.zone_mode || "Z1";
    ctx.fillStyle = colors[z] || colors.Z1;
    ctx.fillRect(x0, y, w, h);
  }
}
