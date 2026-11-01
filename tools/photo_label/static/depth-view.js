// Depth tabs: photo depth (server), Google depth (offscreen render), the fit between them, tab chrome.
// ---------- depth view ----------
// Photo depth (server, Depth Anything: relative inverse depth), Google depth (offscreen render at the solved camera,
// forward-z metres) and the server's per-photo fit: metres = a / (disp - b). All grids cover the whole photo.
import { api, getJson, postJson } from "./api.js";
import { ctx, draw, fit } from "./draw.js";
import { dvOrient, orFill } from "./orient.js";
import { renderAt } from "./registration.js";
import { DV_TABS, app, dv, mdl, st } from "./state.js";
import { $, prefSet, status, unb64f32 } from "./util.js";

export const GD_GRID_W = 200, D_NEAR = 30, D_FAR = 3000, D_DIFF = 0.5;
export function turbo(t) {          // polynomial fit of the turbo map, t in 0..1 -> [r,g,b] 0..255
  const c = x => Math.round(255 * Math.min(1, Math.max(0, x)));
  return [c(0.13572138 + t * (4.61539260 + t * (-42.66032258 + t * (132.13108234 + t * (-152.94239396 + t * 59.28637943))))),
          c(0.09140261 + t * (2.19418839 + t * (4.84296658 + t * (-14.18503333 + t * (4.27729857 + t * 2.82956604))))),
          c(0.10667330 + t * (12.64194608 + t * (-60.58204836 + t * (110.36276771 + t * (-89.90310912 + t * 27.34824973)))))];
}
export const TURBO = Array.from({ length: 256 }, (_, i) => turbo(i / 255));
// metres -> colour: log distance D_NEAR..D_FAR, near = warm
export const distT = m => 1 - Math.min(1, Math.max(0, Math.log(m / D_NEAR) / Math.log(D_FAR / D_NEAR)));
export const distRgb = m => TURBO[Math.round(distT(m) * 255)];
export function divRgb(d) {         // relative difference, clamped to +-D_DIFF: blue (photo nearer) - white - red (photo farther)
  const t = Math.min(1, Math.max(-1, d / D_DIFF)), a = Math.abs(t), end = t < 0 ? [33, 102, 172] : [178, 24, 43];
  return end.map(e => Math.round(255 + (e - 255) * a));
}
export const geoidFor = () => st.geoid ?? mdl.geoid ?? 50;          // as autoRegister: its last geoid, else the model overlay's, else 50
export const gKey = () => st.photo && app.camPose ? `${app.curRegion}|${st.photo.path}|${geoidFor()}|${JSON.stringify(app.camPose)}` : null;
export const curG = () => { const k = gKey(); return k ? dv.gc.get(k) || null : null; };
export const sampleG = (a, w, h, u, v) => a[Math.min(h - 1, Math.max(0, Math.floor(v * h))) * w + Math.min(w - 1, Math.max(0, Math.floor(u * w)))];
export function photoM(u, v) {      // photo depth in metres at normalised photo coords; NaN without a fit
  const g = curG(), p = dv.p;
  if (!g || !g.fit || g.fit.fail || !p || p.state !== "ready") return NaN;
  const d = sampleG(p.disp, p.w, p.h, u, v);
  return d > g.fit.b ? g.fit.a / (d - g.fit.b) : NaN;
}
export function googleM(u, v) { const g = curG(); return g ? sampleG(g.depth, g.w, g.h, u, v) : NaN; }
export function gridCanvas(w, h, fn) {   // fn(i) -> [r,g,b] or null (transparent)
  const c = document.createElement("canvas"); c.width = w; c.height = h;
  const g = c.getContext("2d"), im = g.createImageData(w, h), d = im.data;
  for (let i = 0; i < w * h; i++) { const q = fn(i); if (q) { d[4 * i] = q[0]; d[4 * i + 1] = q[1]; d[4 * i + 2] = q[2]; d[4 * i + 3] = 255; } }
  g.putImageData(im, 0, 0); return c;
}
export function dvNeedCam() {
  if (app.tiles3d === false) return "Google 3D tiles are not available (no API key on the server)";
  if (!app.camPose || app.camPose.elev_m == null) return "Solve the camera first";
  return null;
}
export function dvNeedPhoto() {
  const p = dv.p;
  if (!p) return "Loading photo depth...";
  if (p.state === "missing") return "No depth computed for this photo yet";
  if (p.state === "running") return "Computing photo depth on the server...";
  if (p.state === "error") return "Photo depth failed: " + (p.error || "unknown error");
  return null;
}
export const fitLabel = f => `fit n ${f.n}, median error ${((Math.exp(f.err) - 1) * 100).toFixed(0)}%`;
// Layer for the current tab, cached until a grid, fit, tab or camera changes: { canvas|null, msg, kind: m|rel|diff, corner }
export function dvLayer() {
  const g = curG(), key = `${dv.tab}|${dv.ver}|${gKey()}|${dv.src}|${dv.view}`;
  if (dv.layer && dv.layer.key === key) return dv.layer;
  const L = { key, canvas: null, msg: "", kind: null, corner: "" };
  const gwait = dv.gerr ? "Google depth failed: " + dv.gerr + " (press Render Google depth to retry)" : dv.gbusy ? "Rendering Google depth..." : "Press Render Google depth (it fetches Google 3D tiles)";
  if (dv.tab === "gd") {
    L.msg = dvNeedCam() || (g ? "" : gwait);
    if (g) { L.kind = "m"; L.canvas = gridCanvas(g.w, g.h, i => g.depth[i] > 0 ? distRgb(g.depth[i]) : null); }
  } else if (dv.tab === "pd") {
    L.msg = dvNeedPhoto() || "";
    if (!L.msg) {
      const p = dv.p;
      if (g && g.fit && !g.fit.fail) {
        const { a, b } = g.fit; L.kind = "m"; L.corner = fitLabel(g.fit);
        L.canvas = gridCanvas(p.w, p.h, i => p.disp[i] > b ? distRgb(a / (p.disp[i] - b)) : null);
      } else {
        L.kind = "rel"; const span = p.hi - p.lo || 1;
        L.canvas = gridCanvas(p.w, p.h, i => Number.isFinite(p.disp[i]) ? TURBO[Math.round(255 * Math.min(1, Math.max(0, (p.disp[i] - p.lo) / span)))] : null);
        L.corner = g && g.fit ? "Fit failed: " + g.fit.fail + " (relative depth)" : "Relative depth (bigger = nearer); metres need the Google depth";
      }
    }
  } else if (dv.tab === "or") {
    orFill(L, g, gwait);
  } else {
    L.msg = dvNeedPhoto() || dvNeedCam() || (!g ? gwait : !g.fit ? "Fitting photo depth to Google..." : g.fit.fail ? "Fit failed: " + g.fit.fail : "");
    if (!L.msg) {
      L.kind = "diff"; L.corner = fitLabel(g.fit);
      L.canvas = gridCanvas(g.w, g.h, i => {
        const gm = g.depth[i]; if (!(gm > 0)) return null;
        const pm = photoM(((i % g.w) + .5) / g.w, (Math.floor(i / g.w) + .5) / g.h);
        return pm > 0 ? divRgb(pm / gm - 1) : null;
      });
    }
  }
  return dv.layer = L;
}
export function drawBase() {        // the photo, or the depth layer over dark grey, in the photo's place
  const v = st.view, w = st.img.width * v.s, h = st.img.height * v.s;
  if (dv.tab === "photo") { dvChrome(null); ctx.drawImage(st.img, v.ox, v.oy, w, h); return; }
  const L = dvLayer(); dvChrome(L);
  if (dv.tab === "or") {       // orientation: a translucent layer over the photo
    ctx.drawImage(st.img, v.ox, v.oy, w, h);
    if (L.canvas) { ctx.save(); ctx.globalAlpha = 0.6; ctx.imageSmoothingEnabled = false; ctx.drawImage(L.canvas, v.ox, v.oy, w, h); ctx.restore(); }
    else if (L.msg) { ctx.save(); ctx.font = "14px system-ui"; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.shadowColor = "#000"; ctx.shadowBlur = 6; ctx.fillStyle = "#fff"; ctx.fillText(L.msg, v.ox + w / 2, v.oy + h / 2); ctx.restore(); }
    return;
  }
  ctx.fillStyle = "#2a2a2a"; ctx.fillRect(v.ox, v.oy, w, h);
  if (L.canvas) { ctx.imageSmoothingEnabled = true; ctx.drawImage(L.canvas, v.ox, v.oy, w, h); }
  else if (L.msg) { ctx.fillStyle = "#ddd"; ctx.font = "14px system-ui"; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText(L.msg, v.ox + w / 2, v.oy + h / 2); }
}
export const LEG = { m: ["30 m", "300 m", "3 km"], rel: ["near", "", "far"], diff: ["-50%", "0", "+50%"], pitch: ["0\u00b0", "30\u00b0", "60\u00b0"] };
export function dvChrome(L) {       // legend, corner label and Compute button follow the current layer
  const kind = L && L.kind, corner = L ? L.corner : "", comp = dv.tab !== "photo" && dv.tab !== "gd" && !!dv.p && dv.p.state === "missing",
        gbtn = ["gd", "df", "or"].includes(dv.tab) && !dvNeedCam() && !curG() && !dv.gbusy;
  const sw = L && L.sw || "", sig = `${kind}|${corner}|${comp}|${sw}|${gbtn}`; if (sig === dv.chrome) return; dv.chrome = sig;
  $("oCtl").style.display = dv.tab === "or" ? "flex" : "none";
  $("oLeg").style.display = sw ? "block" : "none"; if (sw) $("oLeg").innerHTML = sw;
  $("dLeg").style.display = kind ? "block" : "none";
  if (kind) {
    const g = $("dLeg").querySelector("canvas").getContext("2d"), im = g.createImageData(160, 1);
    for (let x = 0; x < 160; x++) {
      const f = x / 159, q = kind === "diff" ? divRgb((f * 2 - 1) * D_DIFF) : kind === "pitch" ? TURBO[Math.round(f * 255)] : TURBO[Math.round((1 - f) * 255)];
      im.data.set([q[0], q[1], q[2], 255], 4 * x);
    }
    for (let y = 0; y < 10; y++) g.putImageData(im, 0, y);
    $("dLeg").querySelectorAll("span").forEach((s, i) => { s.textContent = LEG[kind][i]; });
  }
  $("dCorner").style.display = corner ? "block" : "none"; $("dCorner").textContent = corner;
  $("dCompute").style.display = comp ? "block" : "none";
  $("gRender").style.display = gbtn ? "block" : "none";
}
export function setTab(t) {
  if (!DV_TABS.includes(t)) t = "photo";
  dv.tab = t; dv.gerr = null; dv.layer = null; dv.chrome = "";
  prefSet("photoLabelTab", t);
  document.querySelectorAll("#dTabs button").forEach(b => b.classList.toggle("on", b.dataset.t === t));
  draw(); dvRefresh();
}
export function dvReset(all) {      // photo switched, or the region cleared (all): drop the photo depth, and the Google cache on a region change
  dv.seq++; dv.p = null; dv.loading = false; clearTimeout(dv.timer); dv.layer = null; dv.chrome = ""; dv.ver++; dv.gerr = null;
  if (all) dv.gc.clear();
}
// Fetch whatever the current tab needs. Google depth is not rendered here: it fetches 3D tiles, so the
// "Render Google depth" button (dvGoogle) starts it.
export function dvRefresh() {
  if (!st.photo || dv.tab === "photo") return;
  if (dv.tab !== "gd") dvLoadDepth();
  if (dv.tab === "or") dvOrient();
}
export async function dvLoadDepth() {
  const ph = st.photo, seq = dv.seq;
  if (!ph || dv.loading || (dv.p && dv.p.state === "ready")) return;
  dv.loading = true;
  try {
    const r = await getJson(api("/api/depth?photo=" + encodeURIComponent(ph.path)));
    if (seq !== dv.seq) return;
    if (!r.ok) dv.p = { path: ph.path, state: "error", error: r.error };
    else if (r.state === "ready") {
      const disp = unb64f32(r.disp), s = [];
      for (let i = 0; i < disp.length; i += 7) if (Number.isFinite(disp[i])) s.push(disp[i]);
      s.sort((a, b) => a - b);
      dv.p = { path: ph.path, state: "ready", w: r.w, h: r.h, disp, lo: s[Math.floor(.02 * s.length)] ?? 0, hi: s[Math.floor(.98 * s.length)] ?? 1 };
      for (const g of dv.gc.values()) g.fit = null;           // a fit belongs to the grid it was made against
    } else dv.p = { path: ph.path, state: r.state, error: r.error };
  } catch (e) { if (seq === dv.seq) dv.p = { path: ph.path, state: "error", error: e.message }; }
  if (seq !== dv.seq) return;
  dv.loading = false; dv.ver++; draw();
  if (dv.p.state === "running") { status("Photo depth: computing on the server..."); dv.timer = setTimeout(dvLoadDepth, 3000); }
  else if (dv.p.state === "ready") dvFit();
  else dvOrient();            // no photo depth available: orientation can go on with Google alone
}
export async function computeDepth() {
  const ph = st.photo, seq = dv.seq; if (!ph) return;
  $("dCompute").disabled = true;
  try {
    const r = await postJson("/api/depth/compute", { photo: ph.path });
    if (seq !== dv.seq) return;
    dv.p = r.ok ? { path: ph.path, state: r.state } : { path: ph.path, state: "error", error: r.error };
    dv.ver++; draw();
    if (r.ok) { status("Photo depth: computing on the server..."); dv.timer = setTimeout(dvLoadDepth, r.state === "ready" ? 0 : 3000); }
  } catch (e) { status("Compute depth failed: " + e.message); }
  finally { $("dCompute").disabled = false; }
}
// Started by the "Render Google depth" button (Google depth / Difference / Orientation tabs).
export async function dvGoogle() {
  const ph = st.photo, seq = dv.seq;
  if (!ph || dvNeedCam() || dv.gbusy || curG()) return;
  if (app.autoBusy) { status("Auto register is running; render Google depth when it finishes."); return; }
  const key = gKey(); dv.gbusy = true; dv.gerr = null; dv.layer = null; draw();
  status("Google depth: rendering at the solved camera (waiting for tiles)...");
  let ok = false;
  try {
    const rd = await renderAt({ ...camPose, width: ph.w, height: ph.h }, geoidFor(), GD_GRID_W);
    if (seq === dv.seq) {
      dv.gc.set(key, { depth: unb64f32(rd.depth), b64: rd.depth, w: rd.depth_w, h: rd.depth_h, fit: null });
      if (dv.gc.size > 4) dv.gc.delete(dv.gc.keys().next().value);
      ok = true; status("Google depth ready" + (rd.loaded ? "." : " (tiles were still loading)."));
    }
  } catch (e) { dv.gerr = e.message || String(e); status("Google depth failed: " + dv.gerr); }
  finally { dv.gbusy = false; }      // the render viewer stays: its tiles serve the next render
  dv.layer = null; dv.ver++; draw();
  if (seq !== dv.seq || (ok && gKey() !== key)) dvRefresh();   // the photo or camera changed while rendering
  else dvFit();
}
export async function dvFit() {
  const g = curG(), p = dv.p, seq = dv.seq;
  if (!g || !p || p.state !== "ready" || g.fit || g.fitting) return;
  g.fitting = true; status("Fitting photo depth to Google depth...");
  try {
    const r = await postJson("/api/depth/fit", { photo: p.path, depth: g.b64, depth_w: g.w, depth_h: g.h });
    if (seq !== dv.seq) return;
    g.fit = r.ok ? { a: r.a, b: r.b, n: r.n, err: r.err_median_log } : { fail: r.reason || r.error || "fit failed" };
    status(r.ok ? "Depth " + fitLabel(g.fit) + "." : "Depth fit failed: " + g.fit.fail);
  } catch (e) { g.fit = { fail: e.message }; status("Depth fit failed: " + e.message); }
  finally { g.fitting = false; }
  if (seq === dv.seq) { dv.layer = null; dv.ver++; draw(); dvOrient(); }
}
export function depthHover(x, y) {
  if (x < 0 || y < 0 || x >= st.img.width || y >= st.img.height) return;
  const u = x / st.img.width, v = y / st.img.height, pm = photoM(u, v), gm = googleM(u, v), f = m => m > 0 ? m.toFixed(0) + " m" : "-";
  status(`photo ${f(pm)}, Google ${f(gm)}, difference ${pm > 0 && gm > 0 ? ((pm / gm - 1) * 100).toFixed(0) + "%" : "-"}`);
}
