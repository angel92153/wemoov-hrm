// Stubs para futuros renderizados en canvas
export function clearCanvas(cnv){
  const ctx = cnv?.getContext?.('2d');
  if (!ctx) return;
  const dpr = window.devicePixelRatio || 1;
  const w = (cnv.clientWidth || 300) * dpr;
  const h = (cnv.clientHeight || 14) * dpr;
  cnv.width = Math.max(1, Math.floor(w));
  cnv.height= Math.max(1, Math.floor(h));
  ctx.clearRect(0,0,cnv.width,cnv.height);
}
