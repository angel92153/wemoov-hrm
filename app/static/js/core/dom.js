// Helpers DOM muy simples
export const qs  = (sel, ctx=document) => ctx.querySelector(sel);
export const qsa = (sel, ctx=document) => Array.from(ctx.querySelectorAll(sel));
export const on  = (el, ev, cb, opts)   => el.addEventListener(ev, cb, opts);
