// ---------- orientation tab: surfaces, wall facing, roof pitch from depth (server: /api/depth/orient) ----------
// Response per source (photo | google): { w, h, cls u8 (0 none, 1 ground, 2 flat roof, 3 pitched roof, 4 wall), pitch f32 deg, az f32 compass deg, buildings }.
import { api, postJson } from "./api.js";
import { TURBO, curG, dvNeedCam, gKey, gridCanvas } from "./depth-view.js";
import { bldAt, draw } from "./draw.js";
import { dv, st } from "./state.js";
import { $, prefSet, status, unb64f32, unb64u8 } from "./util.js";

export const OR_CLS = ["none", "ground", "flat roof", "pitched roof", "wall"];
export const OR_SURF = [null, [150, 130, 110], [10, 132, 255], [255, 149, 0], [160, 230, 150]];
export const OR_PITCH_MAX = 60;
export const hslRgb = (h, s, l) => { const k = n => (n + h / 30) % 12, a = s * Math.min(l, 1 - l), f = n => l - a * Math.max(-1, Math.min(k(n) - 3, 9 - k(n), 1)); return [Math.round(255 * f(0)), Math.round(255 * f(8)), Math.round(255 * f(4))]; };
export const OR_HUE = Array.from({ length: 360 }, (_, i) => hslRgb(i, .85, .55));
export const OR_SW_SURF = '<span class="sw" style="background:rgb(150,130,110)"></span>ground<span class="sw" style="background:rgb(10,132,255)"></span>flat roof<span class="sw" style="background:rgb(255,149,0)"></span>pitched roof<span class="sw" style="background:rgb(160,230,150)"></span>wall';
export const OR_SW_WALL = '<div class="ring"><i></i><b style="left:23px;top:-3px">N</b><b style="right:-1px;top:19px">E</b><b style="left:23px;bottom:-3px">S</b><b style="left:-1px;top:19px">W</b></div>';
export const compassWord = a => ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][Math.round((((a % 360) + 360) % 360) / 45) % 8];
export const compassTxt = a => `${compassWord(a)} ${Math.round(((a % 360) + 360) % 360) % 360}°`;
export function orSource(R) {
  if (!R) return null;
  const o = dv.src === "photo" ? "google" : "photo";
  return R[dv.src] ? { s: R[dv.src], name: dv.src } : R[o] ? { s: R[o], name: o } : null;
}
export const orDecode = s => s._d || (s._d = { cls: unb64u8(s.cls), pitch: unb64f32(s.pitch), az: unb64f32(s.az) });
export function orRgb(d, view) {
  if (view === "wall") return i => { const c = d.cls[i], a = d.az[i]; return (c === 4 || c === 3) && Number.isFinite(a) ? OR_HUE[Math.round(((a % 360) + 360) % 360) % 360] : null; };
  if (view === "pitch") return i => { const c = d.cls[i], p = d.pitch[i]; return (c === 2 || c === 3) && Number.isFinite(p) ? TURBO[Math.round(255 * Math.min(1, Math.max(0, p / OR_PITCH_MAX)))] : null; };
  return i => OR_SURF[d.cls[i]] || null;
}
export const orReady = g => { const p = dv.p; return !!p && p.state !== "running" && !(p.state === "ready" && !g.fit); };   // fit settled, or no photo depth to wait for
export const orKey = g => { const f = g.fit && !g.fit.fail ? g.fit : null; return `${gKey()}|${f ? f.a + "," + f.b : "nofit"}|${st.bld.length}`; };
export function orFill(L, g, gwait) {
  L.msg = dvNeedCam() || (!g ? gwait : "");
  if (!L.msg && !orReady(g)) L.msg = !dv.p ? "Loading photo depth..." : dv.p.state === "running" ? "Computing photo depth on the server..." : "Fitting photo depth to Google...";
  if (!L.msg) { const o = g.orient; if (!o || o.state === "busy") L.msg = "Computing orientation..."; else if (o.state === "err") L.msg = "Orientation failed: " + o.error; }
  if (L.msg) return;
  const R = g.orient.r, sel = orSource(R);
  if (!sel) { L.msg = "No orientation result for this photo"; return; }
  const d = orDecode(sel.s);
  L.canvas = gridCanvas(sel.s.w, sel.s.h, orRgb(d, dv.view));
  if (dv.view === "pitch") L.kind = "pitch"; else L.sw = dv.view === "wall" ? OR_SW_WALL : OR_SW_SURF;
  const ag = R.agree != null ? ` · agree ${Math.round(R.agree <= 1 ? R.agree * 100 : R.agree)}%` : "";
  const nm = n => n === "photo" ? "Photo" : "Google";
  L.corner = (sel.name === dv.src ? nm(sel.name) + " depth" : `${nm(sel.name)} depth (${nm(dv.src)} unavailable)`) + ag;
}
export async function dvOrient() {
  if (dv.tab !== "or" || !st.photo) return;
  const g = curG(); if (!g || !orReady(g) || dvNeedCam()) return;
  const key = orKey(g); if (g.orient && g.orient.key === key) return;
  const o = g.orient = { key, state: "busy" }, seq = dv.seq, ph = st.photo, f = g.fit && !g.fit.fail ? g.fit : null;
  status("Orientation: computing...");
  const body = { photo: ph.path, camera: { ...camPose, width: ph.w, height: ph.h }, depth: g.b64, depth_w: g.w, depth_h: g.h, grid_w: 320,
    buildings: st.bld.map(b => ({ id: b.id, rings: b.contours, dist: b.dist_m })) };
  if (f) { body.a = f.a; body.b = f.b; }
  try {
    const r = await postJson("/api/depth/orient", body);
    if (r.ok) { o.state = "ok"; o.r = r; status("Orientation ready."); }
    else { o.state = "err"; o.error = r.reason || r.error || "failed"; status("Orientation failed: " + o.error); }
  } catch (e) { o.state = "err"; o.error = e.message || String(e); status("Orientation failed: " + o.error); }
  if (seq !== dv.seq) { if (g.orient === o) delete g.orient; return; }
  dv.layer = null; dv.ver++; draw();
}
export function orMarks() {
  document.querySelectorAll("#oSrc button").forEach(b => b.classList.toggle("on", b.dataset.v === dv.src));
  document.querySelectorAll("#oView button").forEach(b => b.classList.toggle("on", b.dataset.v === dv.view));
}
export function orSet(key, val) {
  dv[key] = val; dv.layer = null; dv.chrome = "";
  prefSet(key === "src" ? "photoLabelOrSrc" : "photoLabelOrView", val);
  orMarks(); draw();
}
export function orHover(x, y) {
  if (x < 0 || y < 0 || x >= st.img.width || y >= st.img.height) return;
  const g = curG(), o = g && g.orient; if (!o || o.state !== "ok") return;
  const sel = orSource(o.r); if (!sel) return;
  const s = sel.s, d = orDecode(s), i = Math.min(s.h - 1, Math.floor(y / st.img.height * s.h)) * s.w + Math.min(s.w - 1, Math.floor(x / st.img.width * s.w));
  const c = d.cls[i], pt = d.pitch[i], az = d.az[i];
  let t = OR_CLS[c] || "none";
  if (Number.isFinite(pt)) t += `, pitch ${Math.round(pt)}°`;
  if (Number.isFinite(az)) t += `, facing ${compassTxt(az)}`;
  const b = bldAt(x, y), sm = b && s.buildings && s.buildings[b.id];
  if (sm) {
    const walls = [...new Set((sm.wall_az || []).map(compassWord))].join("/");
    const roof = sm.roof === "pitched" ? `pitched${sm.pitch_deg != null ? " " + Math.round(sm.pitch_deg) + "°" : ""}` : sm.roof;
    t += `; building ${b.id}: ${roof}${walls ? ", walls " + walls : ""} (${sel.name})`;
  }
  status(t);
}
