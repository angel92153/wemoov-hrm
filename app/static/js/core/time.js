// Formato 00:00
export function fmtMMSS(sec){
  sec = Math.max(0, Math.floor(sec||0));
  const mm = String(Math.floor(sec/60)).padStart(2,'0');
  const ss = String(sec%60).padStart(2,'0');
  return `${mm}:${ss}`;
}

// Reloj de la topbar que “salta” justo al minuto siguiente
export function startClock(clockEl){
  function tick(){
    const n = new Date();
    const hh = String(n.getHours()).padStart(2,'0');
    const mm = String(n.getMinutes()).padStart(2,'0');
    clockEl.textContent = `${hh}:${mm}`;
    const msToNextMinute = (60 - n.getSeconds())*1000 - n.getMilliseconds();
    clearTimeout(tick._t);
    tick._t = setTimeout(tick, Math.max(0, msToNextMinute));
  }
  tick();
}
