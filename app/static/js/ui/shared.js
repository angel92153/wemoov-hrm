// static/js/ui/shared.js
// -----------------------------------------------------------------------------
// Helpers compartidos entre LIVE y SUMMARY: colores, nick fit/placement,
// layout del grid, utilidades de timing y escalado de texto.
// -----------------------------------------------------------------------------

// DOM / CSS roots
export const ROOT = document.documentElement;

// Lazy getters
export function getGRID() {
  return document.getElementById("grid");
}

// CSSOM cacheado
let _ROOT_CSS = null;
export function getRootCSS() {
  if (!_ROOT_CSS) _ROOT_CSS = getComputedStyle(ROOT);
  return _ROOT_CSS;
}

// requestIdle polyfill
export const requestIdle = (cb) =>
  (window.requestIdleCallback || window.requestAnimationFrame)(cb);

// -----------------------------------------------------------------------------
// Colores de zona (core.css manda)
// -----------------------------------------------------------------------------
let _ZONE_COLORS = null;
export function getZoneColors() {
  if (!_ZONE_COLORS) {
    const css = getRootCSS();
    _ZONE_COLORS = {
      Z1: css.getPropertyValue("--z1").trim() || "#3a3a3a",
      Z2: css.getPropertyValue("--z2").trim() || "#1e3a8a",
      Z3: css.getPropertyValue("--z3").trim() || "#166534",
      Z4: css.getPropertyValue("--z4").trim() || "#7c3aed",
      Z5: css.getPropertyValue("--z5").trim() || "#b91c1c",
    };
  }
  return _ZONE_COLORS;
}

// Clase CSS por zona
export const zoneClass = (z) =>
  ({ Z1: "z1", Z2: "z2", Z3: "z3", Z4: "z4", Z5: "z5" }[z] || "z1");

// % esfuerzo a partir de HR/HRmax
export const pctFrom = (hr, hrmax) =>
  typeof hr === "number" &&
  typeof hrmax === "number" &&
  hrmax > 0
    ? Math.max(0, Math.min(100, Math.floor((hr * 100) / hrmax)))
    : null;

// -----------------------------------------------------------------------------
// Nick helpers
// -----------------------------------------------------------------------------
export function formatNickText(apodo, dev) {
  const t = (apodo ?? "").trim();
  if (!t) return `ID\u00A0${dev}`;
  if (/^ID\s+\S+$/i.test(t)) return t.replace(/\s+/, "\u00A0");
  return t;
}

export function fitNick(el) {
  if (!el) return;
  const card = el.closest(".card");
  if (!card) return;

  const css = getRootCSS();
  const w = card.clientWidth * 0.92;

  // bases desde core.css
  const baseNick = parseFloat(css.getPropertyValue("--base-nick-1")) || 112;
  const textScale = parseFloat(css.getPropertyValue("--text-scale")) || 1;
  const headerScale = parseFloat(css.getPropertyValue("--header-scale")) || 1;

  const MIN_PX = 10;
  // 👇 muy importante: incluimos headerScale
  const MAX_PX = baseNick * textScale * headerScale;

  // medir sin ellipsis
  const prevOverflow = el.style.overflow;
  const prevTO = el.style.textOverflow;
  el.style.overflow = "visible";
  el.style.textOverflow = "clip";

  let lo = MIN_PX;
  let hi = MAX_PX;
  el.style.fontSize = hi + "px";

  if (el.scrollWidth > w) {
    // búsqueda binaria para encontrar el mayor font-size que cabe
    for (let i = 0; i < 16; i++) {
      const mid = Math.floor((lo + hi) / 2);
      el.style.fontSize = mid + "px";
      if (el.scrollWidth <= w) {
        lo = mid;
      } else {
        hi = mid - 1;
      }
    }
    el.style.fontSize = lo + "px";
  }

  // restaurar
  el.style.overflow = prevOverflow || "clip";
  el.style.textOverflow = prevTO || "ellipsis";
}

export function placeNick(el) {
  if (!el) return;
  const card = el.closest(".card");
  if (!card) return;
  const pctEl = card.querySelector(".pct");

  const css = getRootCSS();
  const zonebarH = parseFloat(css.getPropertyValue("--zonebar-h")) || 14;
  const topY = zonebarH + 8;

  const bottomY = pctEl ? pctEl.offsetTop : card.clientHeight * 0.45;
  const nickH = el.getBoundingClientRect().height || 0;
  const avail = Math.max(0, bottomY - topY);
  const topPx = nickH <= avail ? topY + (avail - nickH) / 2 : topY;

  el.style.position = "absolute";
  el.style.top = `${topPx}px`;
  el.style.left = "50%";
  el.style.transform = "translateX(-50%)";
}

// -----------------------------------------------------------------------------
// Escalado global de texto según tamaño de tarjeta
// -----------------------------------------------------------------------------

/**
 * Calcula la dimensión mínima por tarjeta para un grid dado.
 */
function computeMinDimPerCard(grid, cols, rows) {
  const cs = getComputedStyle(grid);
  const gap = parseFloat(cs.gap || cs.rowGap || 0) || 0;

  const gridW = grid.clientWidth;
  const gridH = grid.clientHeight;

  const cardW = Math.max(0, (gridW - (cols - 1) * gap) / cols);
  const cardH = Math.max(0, (gridH - (rows - 1) * gap) / rows);

  return Math.min(cardW, cardH);
}

/**
 * Aplica --text-scale comparando el layout actual contra 1×1
 * y ajusta casos especiales (2,7,8 → métricas; 13–16 → cabecera).
 */
export function applyResponsiveTextScale(grid, cols, rows, count) {
  if (!grid || !cols || !rows) return;

  // 1) scale base
  const baseMin = computeMinDimPerCard(grid, 1, 1);
  const curMin = computeMinDimPerCard(grid, cols, rows);

  let scale = 1;
  if (baseMin > 0) {
    scale = curMin / baseMin;
  }

  // límites globales
  scale = Math.max(0.35, Math.min(1, scale));
  ROOT.style.setProperty("--text-scale", String(scale));

  // 2) valores por defecto
  let metricScale = 1;
  let headerScale = 1;

  // --- CASO A: 2, 7 y 8 tarjetas → bajar SOLO métricas
  if (count === 2 || count === 7 || count === 8) {
    metricScale = 0.75;
  }

  // --- CASO B: 13, 14, 15, 16 → bajar SOLO cabecera (nick + %)
  if (count >= 13 && count <= 24) {
    headerScale = 0.75;
  }

  // 3) aplicar
  ROOT.style.setProperty("--metric-scale", String(metricScale));
  ROOT.style.setProperty("--header-scale", String(headerScale));
}

// -----------------------------------------------------------------------------
// Layout del grid
// -----------------------------------------------------------------------------
export function layoutForCount(n, onAfterLayout) {
  const GRID = getGRID();
  if (!GRID) return null;

  // sin tarjetas -> deja todo en 1x1 y escala por defecto
  if (n <= 0) {
    GRID.style.gridTemplateColumns = "";
    GRID.style.gridTemplateRows = "";
    applyResponsiveTextScale(GRID, 1, 1, 0);
    return { cols: 1, rows: 1, heightPerCard: GRID.clientHeight };
  }

  let cols, rows;
  if (n <= 2) {
    cols = n;
    rows = 1;
  } else if (n <= 4) {
    cols = 2;
    rows = 2;
  } else if (n <= 6) {
    cols = 3;
    rows = 2;
  } else if (n <= 8) {
    cols = 4;
    rows = 2;
  } else if (n === 9) {
    cols = 3;
    rows = 3;
  } else if (n <= 12) {
    cols = 4;
    rows = 3;
  } else {
    // 13–16 (o más) -> 4x4
    cols = 4;
    rows = 4;
  }

  GRID.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;
  GRID.style.gridTemplateRows = `repeat(${rows}, 1fr)`;

  // altura por fila
  const css = getComputedStyle(GRID);
  const gap = parseFloat(css.gap) || 0;
  const h = GRID.clientHeight;
  const heightPerCard = Math.max(0, (h - (rows - 1) * gap) / rows);
  GRID.querySelectorAll(".card").forEach((c) => {
    c.style.height = `${heightPerCard}px`;
  });

  // aplicar escalado global ahora que sabemos cols/rows
  applyResponsiveTextScale(GRID, cols, rows, n);

  // recolocar nicks
  GRID.querySelectorAll(".nick").forEach((el) => {
    fitNick(el);
    placeNick(el);
  });

  if (typeof onAfterLayout === "function") onAfterLayout();

  return { cols, rows, heightPerCard };
}
