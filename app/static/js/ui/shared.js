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

  // si aún no tiene ancho, no podemos medir → salimos
  const cardW = card.clientWidth;
  if (!cardW || cardW <= 0) {
    // por si acaso, quitamos la marca
    el.classList.remove("nick--shrunk");
    return;
  }

  const css = getRootCSS();
  const w = cardW * 0.92;

  // 1) tamaño base del layout (1ª transformación)
  const baseNick = parseFloat(css.getPropertyValue("--base-nick-1")) || 112;
  const textScale = parseFloat(css.getPropertyValue("--text-scale")) || 1;
  const headerScale = parseFloat(css.getPropertyValue("--header-scale")) || 1;
  const targetPx = baseNick * textScale * headerScale;

  // guardar overflow previo
  const prevOverflow = el.style.overflow;
  const prevTO = el.style.textOverflow;
  el.style.overflow = "visible";
  el.style.textOverflow = "clip";

  // 1ª pasada → tamaño ideal
  el.style.fontSize = targetPx + "px";

  // ¿cabe con el tamaño ideal?
  const needsSecondPass = el.scrollWidth > w;

  // valor final
  let finalPx = targetPx;

  if (needsSecondPass) {
    // 2ª pasada: solo si NO cabía
    const MIN_PX = 10;
    let lo = MIN_PX;
    let hi = targetPx;

    for (let i = 0; i < 16; i++) {
      const mid = Math.floor((lo + hi) / 2);
      el.style.fontSize = mid + "px";
      if (el.scrollWidth <= w) {
        lo = mid;
      } else {
        hi = mid - 1;
      }
    }
    finalPx = lo;
    el.style.fontSize = finalPx + "px";
  }

  // restaurar overflow
  el.style.overflow = prevOverflow || "clip";
  el.style.textOverflow = prevTO || "ellipsis";

  // marcar solo si de verdad hubo 2ª pasada (reducción apreciable)
  const EPS = 0.5;
  if (finalPx < targetPx - EPS) {
    el.classList.add("nick--shrunk");
  } else {
    el.classList.remove("nick--shrunk");
  }
}

export function placeNick(el) {
  if (!el) return;
  const card = el.closest(".card");
  if (!card) return;

  // solo aseguramos el contexto;
  // la posición vertical y el transform exacto los manda el CSS (.nick y .nick--shrunk)
  el.style.position = "absolute";
  el.style.left = "50%";
}

// -----------------------------------------------------------------------------
// Escalado global de texto según tamaño de tarjeta
// -----------------------------------------------------------------------------
function computeMinDimPerCard(grid, cols, rows) {
  const cs = getComputedStyle(grid);
  const gap = parseFloat(cs.gap || cs.rowGap || 0) || 0;

  const gridW = grid.clientWidth;
  const gridH = grid.clientHeight;

  const cardW = Math.max(0, (gridW - (cols - 1) * gap) / cols);
  const cardH = Math.max(0, (gridH - (rows - 1) * gap) / rows);

  return Math.min(cardW, cardH);
}

export function applyResponsiveTextScale(grid, cols, rows, count) {
  if (!grid || !cols || !rows) return;

  const baseMin = computeMinDimPerCard(grid, 1, 1);
  const curMin = computeMinDimPerCard(grid, cols, rows);

  let scale = 1;
  if (baseMin > 0) {
    scale = curMin / baseMin;
  }

  scale = Math.max(0.35, Math.min(1, scale));
  ROOT.style.setProperty("--text-scale", String(scale));

  let metricScale = 1;
  let headerScale = 1;

  if (count === 2 || count === 7 || count === 8) {
    metricScale = 0.75;
  }

  if (count >= 13 && count <= 24) {
    headerScale = 0.75;
  }

  ROOT.style.setProperty("--metric-scale", String(metricScale));
  ROOT.style.setProperty("--header-scale", String(headerScale));
}

// -----------------------------------------------------------------------------
// Layout del grid
// -----------------------------------------------------------------------------
export function layoutForCount(n, onAfterLayout) {
  const GRID = getGRID();
  if (!GRID) return null;

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
    cols = 4;
    rows = 4;
  }

  GRID.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;
  GRID.style.gridTemplateRows = `repeat(${rows}, 1fr)`;

  const css = getComputedStyle(GRID);
  const gap = parseFloat(css.gap) || 0;
  const h = GRID.clientHeight;
  const heightPerCard = Math.max(0, (h - (rows - 1) * gap) / rows);
  GRID.querySelectorAll(".card").forEach((c) => {
    c.style.height = `${heightPerCard}px`;
  });

  // aplicar escalado global ahora que sabemos cols/rows
  applyResponsiveTextScale(GRID, cols, rows, n);

  // recolocar nicks con el tamaño final de las cards
  GRID.querySelectorAll(".nick").forEach((el) => {
    fitNick(el);
    placeNick(el);
  });

  if (typeof onAfterLayout === "function") onAfterLayout();

  return { cols, rows, heightPerCard };
}

// -----------------------------------------------------------------------------
// Iconos compartidos
// -----------------------------------------------------------------------------
// Todos devuelven <span class="icon">...</span> para que el CSS actual los pille.

export function makeHeartIcon() {
  const span = document.createElement("span");
  span.className = "icon";
  span.innerHTML = `
    <svg viewBox="0 0 24 24">
      <path d="M12 21s-5.052-3.247-8.106-6.3C1.84 12.646 1 10.97 1 9.2
               1 6.88 2.88 5 5.2 5c1.36 0 2.656.56 3.6 1.56L12 9.04
               l3.2-2.48C16.144 5.56 17.44 5 18.8 5
               21.12 5 23 6.88 23 9.2c0 1.77-.84 3.446-2.894 5.5
               C17.052 17.753 12 21 12 21z"></path>
    </svg>
  `;
  return span;
}

export function makeFlameIcon() {
  const span = document.createElement("span");
  span.className = "icon";
  span.innerHTML =
    '<svg viewBox="0 0 24 24" stroke="currentColor" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8.5 14C8.5 10 12 8 12 4c0-.5-.04-.97-.1-1.4-.04-.3.23-.6.53-.5C16 3 19 6.5 19 11c0 5-3 9-7 9s-7-4-7-9c0-1.3.3-2.6.9-3.7.14-.26.5-.26.7-.02.6.7 1.9 2.2 1.9 3.7z"/></svg>';
  return span;
}

export function makeMoovIcon() {
  const span = document.createElement("span");
  span.className = "icon";
  span.innerHTML = `
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="10"></circle>
      <path d="M7 16V8h2.6l2.4 4 2.4-4H17v8h-2V11.7
               l-2 3.3h-2L9 11.7V16H7z" fill="var(--bg)"></path>
    </svg>
  `;
  return span;
}
