// Small helpers with no dependencies: DOM lookup, status line, colours, base64, stored preferences.

export const COLORS = ["#e6194b","#3cb44b","#ffb400","#4363d8","#f58231","#911eb4","#00b7c3","#f032e6","#9a6324","#800000","#808000","#000075"];
export const $ = id => document.getElementById(id);
export const colorOf = n => COLORS[(n - 1) % COLORS.length];
export const status = t => {
  const e = $("status"); e.textContent = t;
  e.classList.toggle("err", /fail|could not|cannot|no usable|no start|needs the|stopped/i.test(t));
};
export const esc = s => String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
export const hue = id => { let x = 0; for (const c of String(id)) x = (x * 31 + c.charCodeAt(0)) >>> 0; return (x * 47) % 360; };
export const f32b64 = a => { const u = new Uint8Array(a.buffer); let s = ""; for (let i = 0; i < u.length; i += 0x8000) s += String.fromCharCode.apply(null, u.subarray(i, i + 0x8000)); return btoa(s); };
export const unb64u8 = s => { const b = atob(s), u = new Uint8Array(b.length); for (let i = 0; i < b.length; i++) u[i] = b.charCodeAt(i); return u; };
export const unb64f32 = s => new Float32Array(unb64u8(s).buffer);
// Stored preferences; storage can be blocked (private window, thumbnails), so both fail quietly.
export function prefGet(key, def = null) {
  try { const v = localStorage.getItem(key); return v === null ? def : v; } catch (_) { return def; }
}
export function prefSet(key, value) {
  try { localStorage.setItem(key, String(value)); } catch (_) { /* storage can be blocked */ }
}
