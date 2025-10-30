// static/js/config.js
// -----------------------------------------------------------------------------
// Configuración global compartida entre las vistas (LIVE y SUMMARY).
// Se sincroniza automáticamente con los valores reales del backend.
// -----------------------------------------------------------------------------

export const CONFIG = {
  // Valores por defecto (fallback si el backend no responde)
  FADE_DURATION_MS: 60000,     // tiempo de desvanecimiento de tarjetas
  LIVE_RECENT_MS: 5000,        // umbral "reciente" para señal HR
  SUMMARY_SHOW_MS: 600000,     // cuánto tiempo mostrar el resumen (10 min)
  SUMMARY_BUCKET_MS: 5000,     // tamaño del bucket temporal del resumen

  // 🎯 NUEVOS valores para la zoneline (LIVE)
  ZONELINE_BUCKET_MS: 5000,    // tamaño del bucket temporal de zoneline
  ZONELINE_WINDOW_MS: 3600000, // ventana total (1h)
  ZONELINE_REFRESH_MS: 10000   // refresco del canvas (10s)
};

/**
 * Sincroniza la configuración con el backend Flask.
 * El endpoint /live/config devuelve los valores definidos en app/config.py.
 */
export async function loadConfigFromBackend() {
  try {
    const res = await fetch("/live/config", { cache: "no-store" });
    if (!res.ok) {
      console.warn("[config] No se pudo obtener configuración del backend:", res.status);
      return;
    }
    const data = await res.json();

    if (typeof data.fade_ms === "number") CONFIG.FADE_DURATION_MS = data.fade_ms;
    if (typeof data.recent_ms === "number") CONFIG.LIVE_RECENT_MS = data.recent_ms;
    if (typeof data.summary_ms === "number") CONFIG.SUMMARY_SHOW_MS = data.summary_ms;
    if (typeof data.summary_bucket_ms === "number") CONFIG.SUMMARY_BUCKET_MS = data.summary_bucket_ms;
    if (typeof data.zoneline_bucket_ms === "number") CONFIG.ZONELINE_BUCKET_MS = data.zoneline_bucket_ms;
    if (typeof data.zoneline_window_ms === "number") CONFIG.ZONELINE_WINDOW_MS = data.zoneline_window_ms;
    if (typeof data.zoneline_refresh_ms === "number") CONFIG.ZONELINE_REFRESH_MS = data.zoneline_refresh_ms;

    console.debug("[config] cargada desde backend:", CONFIG);
  } catch (err) {
    console.warn("[config] error al cargar desde backend:", err);
  }
}

/**
 * Atajo: actualiza el color global de fase (la variable CSS --phase-color)
 */
export function setPhaseColor(hex) {
  document.documentElement.style.setProperty("--phase-color", hex || "#eab308");
}

// 🔄 Carga automática en cuanto se importe el módulo
loadConfigFromBackend();
