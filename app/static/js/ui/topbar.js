// static/js/ui/topbar.js
// -----------------------------------------------------------------------------
// Control de la barra superior (Topbar):
// - Reloj en tiempo real (sincronizado al minuto)
// - Color de fase (CSS var --phase-color)
// - Temporizador de fase / countdown (mm:ss)
// - Mostrar/ocultar texto de fase
// Expone window.Topbar para uso global.
// -----------------------------------------------------------------------------

(function () {
  const doc = document;
  const root = doc.documentElement;

  // Elementos esperados en el DOM (ids del template)
  const els = {
    timer: doc.getElementById("phaseTimer"),
    phase: doc.getElementById("phaseText"),
    clock: doc.getElementById("clock"),
  };

  // Estado interno
  let clockTO = 0;
  let timerIV = 0;
  let remaining = 0; // segundos restantes mostrados por el timer

  // Utilidades
  const clamp = (n, lo, hi) => Math.min(hi, Math.max(lo, n));
  const mmss = (sec) => {
    sec = Math.max(0, Math.floor(sec || 0));
    const m = String(Math.floor(sec / 60)).padStart(2, "0");
    const s = String(sec % 60).padStart(2, "0");
    return `${m}:${s}`;
  };

  function setPhaseColor(hex) {
    root.style.setProperty("--phase-color", hex || "#eab308");
  }

  // ---- Reloj ----
  function updateClockOnce() {
    if (!els.clock) return;
    const n = new Date();
    const hh = String(n.getHours()).padStart(2, "0");
    const mm = String(n.getMinutes()).padStart(2, "0");
    els.clock.textContent = `${hh}:${mm}`;

    const msToNextMinute = (60 - n.getSeconds()) * 1000 - n.getMilliseconds();
    clearTimeout(clockTO);
    clockTO = setTimeout(updateClockOnce, clamp(msToNextMinute, 250, 10000));
  }

  // ---- Timer fase / countdown ----
  function stopPhaseTimer() {
    clearInterval(timerIV);
    timerIV = 0;
  }

  function renderTimer() {
    if (!els.timer) return;
    els.timer.textContent = mmss(remaining);
    if (els.timer.classList.contains("invisible")) {
      els.timer.classList.remove("invisible");
    }
  }

  function startPhaseTimer({ seconds = 0, key = "", color = null } = {}) {
    // Color
    if (color) setPhaseColor(color);

    // Texto de fase (si viene vacío, no mostramos nada)
    if (els.phase) {
      els.phase.textContent = key ? String(key) : "";
    }

    // Timer
    remaining = Math.max(0, Math.floor(seconds || 0));
    renderTimer();

    stopPhaseTimer();
    timerIV = setInterval(() => {
      remaining = Math.max(0, remaining - 1);
      renderTimer();
    }, 1000);
  }

  function clearPhase() {
    // Limpia texto de fase y oculta timer
    if (els.phase) els.phase.textContent = "";
    if (els.timer) els.timer.classList.add("invisible");
    stopPhaseTimer();
    // Opcional: devolver color por defecto
    setPhaseColor("#eab308");
  }

  // ---- Lifecycle ----
  function onVisibility() {
    if (doc.hidden) {
      clearTimeout(clockTO);
      return;
    }
    updateClockOnce(); // re-sincroniza al volver al foreground
    // No tocamos el timer: live/summary vuelven a llamar periódicamente
  }

  function init() {
    // Reloj
    updateClockOnce();

    // Eventos de visibilidad
    doc.addEventListener("visibilitychange", onVisibility);

    // Limpieza al salir
    window.addEventListener("beforeunload", () => {
      clearTimeout(clockTO);
      stopPhaseTimer();
      doc.removeEventListener("visibilitychange", onVisibility);
    });
  }

  // API pública
  const Topbar = {
    init,
    startPhaseTimer,
    clearPhase,
    setPhaseColor,
  };

  // Exponer global
  window.Topbar = Topbar;

  // Auto-init
  try { init(); } catch {}
})();

/// --- Mini Control Panel (flotante en topbar) ---
(() => {
  const timerBtn = document.getElementById("phaseTimer");
  const panel    = document.getElementById("miniControl");
  if (!timerBtn || !panel) return;

  const sel      = document.getElementById("miniClassSelect");
  const btnStart = document.getElementById("miniStart");
  const btnPrev  = document.getElementById("miniPrev");
  const btnNext  = document.getElementById("miniNext");
  const btnToggle= document.getElementById("miniToggle");

  let hideTimer = null;

  // coloca el panel pegado al borde IZQUIERDO del timer
  function positionPanel() {
    const r = timerBtn.getBoundingClientRect();
    const panelW = panel.offsetWidth || 240;
    // top justo debajo del timer
    panel.style.top  = `${r.bottom + 6}px`;
    // pegado al borde izquierdo del timer
    panel.style.left = `${r.left}px`;
  }

  function togglePanel(show = true) {
    clearTimeout(hideTimer);
    if (show) {
      positionPanel();
      panel.classList.remove("hidden");
      hideTimer = setTimeout(() => panel.classList.add("hidden"), 10000);
    } else {
      panel.classList.add("hidden");
    }
  }

  async function loadClasses() {
    if (!sel) return;
    try {
      const r = await fetch("/control/classes", { cache: "no-store" });
      const data = await r.json();
      const list = Array.isArray(data)
        ? data
        : Array.isArray(data.classes)
          ? data.classes
          : [];

      sel.innerHTML = "";
      const opt0 = document.createElement("option");
      opt0.value = "";
      opt0.textContent = list.length ? "— Selecciona clase —" : "— Sin clases —";
      sel.appendChild(opt0);

      list.forEach((c) => {
        const id    = c.id ?? c.key ?? c.class_id ?? c.slug ?? c.name;
        const label = c.label ?? c.name ?? c.title ?? String(id);
        if (!id) return;
        const o = document.createElement("option");
        o.value = id;
        o.textContent = label;
        sel.appendChild(o);
      });
    } catch (e) {
      sel.innerHTML = '<option value="">(Error)</option>';
    }
  }

  // abrir panel al click en el timer
  timerBtn.addEventListener("click", async (e) => {
    e.stopPropagation();
    await loadClasses();
    togglePanel(true);
  });

  // botones
  if (btnPrev) {
    btnPrev.onclick = async (e) => {
      e.stopPropagation();
      await fetch("/control/prev", { method: "POST" });
      togglePanel(false);
    };
  }
  if (btnNext) {
    btnNext.onclick = async (e) => {
      e.stopPropagation();
      await fetch("/control/next", { method: "POST" });
      togglePanel(false);
    };
  }
  if (btnToggle) {
    btnToggle.onclick = async (e) => {
      e.stopPropagation();
      await fetch("/control/toggle_pause", { method: "POST" });
      togglePanel(false);
    };
  }
  if (btnStart) {
    btnStart.onclick = async (e) => {
      e.stopPropagation();
      const val = sel.value;
      if (!val) {
        // no mostramos mensajes, solo no cerrar
        return;
      }
      await fetch("/control/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ class_id: val }),
      });
      togglePanel(false);
    };
  }

  // click fuera → cerrar
  document.addEventListener("click", (e) => {
    if (!panel.classList.contains("hidden")) {
      if (!panel.contains(e.target) && e.target !== timerBtn) {
        togglePanel(false);
      }
    }
  });

  // reajustar posición al redimensionar
  window.addEventListener("resize", () => {
    if (!panel.classList.contains("hidden")) {
      positionPanel();
    }
  });
})();
