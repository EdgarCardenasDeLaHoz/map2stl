// The photo canvas: view transform, pairs / buildings / ticks overlay, mouse handling.
// ---------- photo canvas ----------
import { api, postJson } from "./api.js";
import { depthHover, drawBase } from "./depth-view.js";
import { finish, invalidate, renderRows, scheduleSave, select } from "./labels.js";
import { drawMapLines, heightText, map, mapHighlight } from "./map2d.js";
import { dvOrient, orHover } from "./orient.js";
import { app, dv, st } from "./state.js";
import { $, colorOf, hue, status } from "./util.js";

export const cv = $("photo"), ctx = cv.getContext("2d");
export function resize() {
  const r = $("photoWrap").getBoundingClientRect();
  cv.width = Math.max(1, r.width); cv.height = Math.max(1, r.height); draw();
}
// A wide photo (aspect > WIDE_ASPECT, e.g. a 2688x540 pano strip) gets the full width and a band as tall as its
// width-fitted height (at least 140 px, at most 45% of the window); the map moves below it.
export const WIDE_ASPECT = 2.5;
export function layoutWide() {
  const ph = st.photo, wide = !!ph && ph.w / ph.h > WIDE_ASPECT;
  document.body.classList.toggle("widePhoto", wide);
  if (wide) document.body.style.setProperty("--wideH", Math.round(Math.min(innerHeight * 0.45, Math.max(140, innerWidth * ph.h / ph.w + 8))) + "px");
  else document.body.style.removeProperty("--wideH");
}
export function fit() {
  if (!st.img) return;
  const s = Math.min(cv.width / st.img.width, cv.height / st.img.height);
  st.view = { s, ox: (cv.width - st.img.width * s) / 2, oy: (cv.height - st.img.height * s) / 2 };
  draw();
}
export const toImg = (px, py) => [(px - st.view.ox) / st.view.s, (py - st.view.oy) / st.view.s];
export const toScr = (x, y) => [x * st.view.s + st.view.ox, y * st.view.s + st.view.oy];

export function disc(x, y, n, pend) {
  const [sx, sy] = toScr(x, y);
  ctx.beginPath(); ctx.arc(sx, sy, 10, 0, 7); ctx.fillStyle = colorOf(n); ctx.fill();
  ctx.lineWidth = 2; ctx.strokeStyle = "#fff"; if (pend) ctx.setLineDash([3, 3]); ctx.stroke(); ctx.setLineDash([]);
  ctx.fillStyle = "#fff"; ctx.font = "bold 11px system-ui"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.fillText(n, sx, sy + 1);
}
export function draw() {
  ctx.clearRect(0, 0, cv.width, cv.height);
  if (!st.img) return;
  const v = st.view;
  drawBase();
  const sol = st.solve;
  if (sol) {
    ctx.textBaseline = "alphabetic";
    // named footprint ticks along the top
    ctx.strokeStyle = "rgba(255,214,10,.9)"; ctx.fillStyle = "#ffd60a"; ctx.lineWidth = 1; ctx.font = "10px system-ui";
    let k = 0;
    for (const t of sol.ticks) {
      const [sx] = toScr(t.x, 0); if (sx < 0 || sx > cv.width) continue;
      ctx.beginPath(); ctx.moveTo(sx, 0); ctx.lineTo(sx, 10); ctx.stroke();
      ctx.save(); ctx.translate(sx + 3, 14 + (k++ % 3) * 0); ctx.rotate(Math.PI / 2); ctx.textAlign = "left";
      ctx.fillText(t.name.slice(0, 24), 0, 0); ctx.restore();
    }
    // predicted x of each pair
    sol.predicted_x.forEach((px, i) => {
      const p = st.pairs[i]; if (!p || px == null) return;
      const [sx] = toScr(px, 0);
      ctx.strokeStyle = colorOf(p.n); ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(sx, 0); ctx.lineTo(sx, cv.height); ctx.stroke();
    });
  }
  if (st.bld.length && $("bldChk").checked) drawBuildings();
  for (const p of st.auto) {
    const [sx, sy] = toScr(p.x, p.y);
    ctx.beginPath(); ctx.arc(sx, sy, 3.5, 0, 7); ctx.strokeStyle = "#00e5ff"; ctx.lineWidth = 1.5; ctx.stroke();
    ctx.beginPath(); ctx.arc(sx, sy, 1.2, 0, 7); ctx.fillStyle = "#00e5ff"; ctx.fill();
  }
  for (const p of st.pairs) disc(p.x, p.y, p.n, false);
  const q = st.pending; if (q && q.x != null) disc(q.x, q.y, q.n, true);
  const s = st.pairs.find(p => p.n === st.sel);
  if (s) { const [sx, sy] = toScr(s.x, s.y); ctx.beginPath(); ctx.arc(sx, sy, 15, 0, 7); ctx.strokeStyle = "#0a84ff"; ctx.lineWidth = 2; ctx.stroke(); }
}

// ---------- projected buildings ----------
export const scrPath = (pts, close) => {
  pts.forEach(([x, y], i) => { const [sx, sy] = toScr(x, y); i ? ctx.lineTo(sx, sy) : ctx.moveTo(sx, sy); });
  if (close) ctx.closePath();
};
export function drawBuildings() {
  ctx.save(); ctx.lineJoin = "round";
  const trace = rs => { ctx.beginPath(); for (const r of rs) scrPath(r, true); };
  const order = st.bld.filter(b => b.id !== st.hiId && b.id !== st.hoverId)
    .concat(st.bld.filter(b => b.id === st.hiId || b.id === st.hoverId));
  for (const b of order) {
    const hi = b.id === st.hiId || b.id === st.hoverId, c = `hsl(${hue(b.id)},85%,${hi ? 65 : 55}%)`;
    trace(b.contours);
    ctx.globalAlpha = hi ? 0.3 : 0.15; ctx.fillStyle = c; ctx.fill();
    ctx.globalAlpha = 1; ctx.lineWidth = hi ? 3 : 1; ctx.strokeStyle = hi ? "#ff453a" : c; ctx.stroke();
    if (b.roof.length && !hi) { trace(b.roof); ctx.lineWidth = 1; ctx.strokeStyle = "rgba(255,255,255,.8)"; ctx.stroke(); }
  }
  drawMeasuredOutlines(order);
  ctx.restore();
}
// A building the pipeline measured tall but published low: a dashed outline at the measured height.
export function drawMeasuredOutlines(list) {
  ctx.save(); ctx.setLineDash([7, 4]); ctx.lineWidth = 2; ctx.strokeStyle = "#ffd60a";
  for (const b of list) {
    if (!b.measured_outline || !b.measured_outline.length) continue;
    ctx.beginPath(); for (const r of b.measured_outline) scrPath(r, true); ctx.stroke();
  }
  ctx.restore();
}
export function bldAt(x, y) {                            // visible building under an image point (id buffer)
  const m = st.idMap; if (!m) return null;
  const px = Math.floor(x), py = Math.floor(y);
  if (px < 0 || py < 0 || px >= m.w || py >= m.h) return null;
  const i = (py * m.w + px) * 4, k = m.data[i] + 256 * m.data[i + 1];
  return k ? st.bld[k - 1] : null;
}
export async function fetchBuildings() {
  st.bld = []; st.idMap = null; st.hoverId = st.hiId = null;
  const q = st.solve && st.solve.pose;
  if (!q || q.elev_m == null) return;
  const ph = st.photo;
  const r = await postJson("/api/project", { camera: q, image_px: [ph.w, ph.h], heights: $("hModeSel").value });
  if (st.solve && st.photo === ph && r.ok) {
    st.idMap = await new Promise(res => {
      if (!r.ids_png) return res(null);
      const im = new Image();
      im.onload = () => { const c = document.createElement("canvas"); c.width = im.width; c.height = im.height;
        const g = c.getContext("2d", { willReadFrequently: true }); g.drawImage(im, 0, 0);
        res({ w: im.width, h: im.height, data: g.getImageData(0, 0, im.width, im.height).data }); };
      im.onerror = () => res(null); im.src = "data:image/png;base64," + r.ids_png; });
    st.bld = r.buildings; draw(); if (dv.tab === "or") dvOrient(); drawMapLines(); status(`${r.buildings.length} buildings in view.`); }
}
export async function loadBuildings() {
  try { await fetchBuildings(); } catch (e) { status("Could not project the buildings: " + (e.message || e)); }
}

// One mousemove handler for the canvas: the building under the pointer, then the depth / orientation readout.
export function onCanvasMove(e) {
  if (app.drag) return;
  const r = cv.getBoundingClientRect(), [x, y] = toImg(e.clientX - r.left, e.clientY - r.top);
  hoverBuilding(x, y);
  if (dv.tab === "photo" || !st.img) return;
  if (dv.tab === "or") orHover(x, y); else depthHover(x, y);
}
export function hoverBuilding(x, y) {
  if (!st.bld.length || !$("bldChk").checked) return;
  const b = bldAt(x, y), id = b ? b.id : null;
  if (id === st.hoverId) return;
  st.hoverId = id; mapHighlight(id); draw();
  status(b ? `${b.name || "building"} (id ${b.id}), height ${heightText(b)}, ${b.dist_m.toFixed(0)} m away` : "");
}
export function onCanvasLeave() { if (st.hoverId != null) { st.hoverId = null; mapHighlight(null); draw(); } }

export function hit(mx, my) {
  const all = st.pairs.concat(st.pending && st.pending.x != null ? [st.pending] : []);
  for (let i = all.length - 1; i >= 0; i--) {
    const [sx, sy] = toScr(all[i].x, all[i].y);
    if (Math.hypot(sx - mx, sy - my) <= 11) return all[i];
  }
  return null;
}
export function onCanvasDown(e) {
  const r = cv.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
  app.drag = { mx, my, moved: false, marker: hit(mx, my), ox: st.view.ox, oy: st.view.oy };
}
export function onDragMove(e) {
  if (!app.drag) return;
  const r = cv.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
  if (!app.drag.moved && Math.hypot(mx - app.drag.mx, my - app.drag.my) < 4) return;
  app.drag.moved = true;
  if (app.drag.marker) { [app.drag.marker.x, app.drag.marker.y] = toImg(mx, my); }
  else { st.view.ox = app.drag.ox + mx - app.drag.mx; st.view.oy = app.drag.oy + my - app.drag.my; }
  draw();
}
export function onCanvasUp(e) {
  if (!app.drag) return;
  const d = app.drag; app.drag = null;
  const r = cv.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
  if (d.moved) { if (d.marker) { invalidate(); renderRows(); scheduleSave(); } return; }
  if (e.target !== cv) return;
  if (d.marker) { select(d.marker.n); return; }
  const [x, y] = toImg(mx, my);
  if (!st.img || x < 0 || y < 0 || x > st.img.width || y > st.img.height) return;
  let p = st.pending;
  if (p && p.x == null) { p.x = x; p.y = y; finish(p); }
  else if (!p) {
    p = { n: st.nextN++, label: "", x, y, lat: null, lon: null, h: null }; st.pending = p; draw();
    status("Now click the matching point on the map.");
  }
}
// The photo's + / - buttons: zoom about the middle of the canvas.
export function zoomBy(k) {
  if (!st.img) return;
  const mx = cv.width / 2, my = cv.height / 2, [ix, iy] = toImg(mx, my);
  st.view.s = Math.min(40, Math.max(0.02, st.view.s * k));
  st.view.ox = mx - ix * st.view.s; st.view.oy = my - iy * st.view.s; draw();
}
export function onWheel(e) {
  e.preventDefault();
  const r = cv.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
  const k = Math.exp(-e.deltaY * 0.0015), [ix, iy] = toImg(mx, my);
  st.view.s = Math.min(40, Math.max(0.02, st.view.s * k));
  st.view.ox = mx - ix * st.view.s; st.view.oy = my - iy * st.view.s; draw();
}
