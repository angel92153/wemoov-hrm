export async function fetchJSON(url, opts={}){
  const ctrl = new AbortController();
  const t = setTimeout(()=>ctrl.abort(), opts.timeout || 8000);
  try{
    const res = await fetch(url, { cache:'no-store', signal:ctrl.signal, ...opts });
    clearTimeout(t);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  }catch(e){
    clearTimeout(t);
    return null;
  }
}

// SSE helper: devuelve la instancia o null si falla
export function sse(url, onMessage, onError){
  try{
    const es = new EventSource(url);
    es.onmessage = (e) => onMessage?.(e.data);
    es.onerror = () => { onError?.(); es.close(); };
    return es;
  }catch(e){
    onError?.();
    return null;
  }
}
