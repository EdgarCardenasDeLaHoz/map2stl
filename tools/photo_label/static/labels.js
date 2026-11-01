// Click-pairs: add / select / remove, the table, the solve button's state, saving the labels.
// A click on the 2D map (h = null) or on the 3D surface (h = ellipsoidal height, m).
import { api, postJson } from "./api.js";
import { draw } from "./draw.js";
import { nameAt } from "./geo.js";
import { camLayer, drawMapLines, dropMarker, mapHighlight, setMapMarker } from "./map2d.js";
import { setPose, setResult, setSaved } from "./chrome.js";
import { app, st } from "./state.js";
import { $, colorOf, esc, status } from "./util.js";

export function mapClick(ll, h = null) {
  if (!st.img) return;
  let p = st.pending;
  if (p && p.lat == null) {
    p.lat = ll.lat; p.lon = ll.lng; p.h = h; p.hGround = null; finish(p);
  } else if (!p) {
    p = { n: st.nextN++, label: "", x: null, y: null, lat: ll.lat, lon: ll.lng, h };
    st.pending = p; setMapMarker(p); status("Now click the matching point on the photo.");
  }
  draw();
}

export function finish(p) {
  st.pending = null;
  if (!p.label) p.label = nameAt(p.lat, p.lon) || ("P" + p.n);
  st.pairs.push(p); st.sel = p.n; setMapMarker(p); invalidate(); renderRows(); draw(); scheduleSave();
  status("Pair " + p.n + " added.");
}

export function select(n) { st.sel = n; renderRows(); draw(); }
export function cancelPending() {
  const p = st.pending; if (!p) return;
  dropMarker(p.n);
  st.pending = null; draw(); status("Pending point cancelled.");
}
export function removePair(n) {
  dropMarker(n);
  st.pairs = st.pairs.filter(p => p.n !== n); if (st.sel === n) st.sel = null;
  invalidate(); renderRows(); draw(); scheduleSave();
}
export function onKeydown(e) {
  if (e.target.matches && e.target.matches("input, select, textarea")) return;
  if (e.key === "Escape") cancelPending();
  else if (e.key === "Delete" && st.sel != null) removePair(st.sel);
}
// Solve needs four pairs; the button says how many are missing.
export function syncSolveBtn() {
  const n = st.pairs.length, b = $("solveBtn");
  b.disabled = n < 4;
  b.title = n < 4 ? `Needs 4 pairs · ${n} so far` : "Solve the camera from the clicked pairs";
  $("solveNeed").textContent = n < 4 ? `Needs 4 pairs · ${n} so far` : "";
  $("pairCount").textContent = n < 4 ? `${n} of 4 needed` : `${n} pairs`;
}
export function invalidate() {
  st.solve = null; st.bld = []; st.idMap = null; st.hoverId = st.hiId = null; mapHighlight(null); app.camPose = null; $("camViewBtn").disabled = true; camLayer.clearLayers(); setResult("");
  syncSolveBtn(); drawMapLines();
}
export function renderRows() {
  const res = st.solve ? st.solve.residuals_px : [], resM = st.solve && st.solve.residuals_m || [],
        hts = st.solve && st.solve.heights_m || [], h0s = st.solve && st.solve.h0_m || [], inf = st.solve && st.solve.in_footprint || [];
  $("rows").innerHTML = st.pairs.map((p, i) => `<tr class="${p.n === st.sel ? "sel" : ""}" data-n="${p.n}">
    <td><span class="dot" style="background:${colorOf(p.n)}"></span> ${p.n}</td>
    <td class="l"><input value="${esc(p.label)}" data-n="${p.n}"></td>
    <td class="pd x">${p.x.toFixed(0)}, ${p.y.toFixed(0)}</td>
    <td class="pd x">${p.lat.toFixed(5)}, ${p.lon.toFixed(5)}${p.h == null ? "" : ", " + p.h.toFixed(1) + " m"}</td>
    <td class="pd x">${hts[i] == null ? "after Solve" : hts[i].toFixed(0) + " m" + (inf[i] ? ` · OSM ${h0s[i].toFixed(0)} m` : "")}</td>
    <td class="x">${res[i] == null ? "after Solve" : res[i].toFixed(1) + " px"}<span class="pd">${resM[i] == null ? "" : " / " + resM[i].toFixed(1) + " m"}</span></td>
    <td class="x"><button class="del" data-del="${p.n}" title="Delete this pair" aria-label="Delete pair ${p.n}">✕</button></td></tr>`).join("") +
    (st.auto.length ? `<tr><td><span class="dot" style="background:#00e5ff"></span></td><td class="x" colspan="2">${st.auto.length} auto pairs (cyan dots on the photo)</td><td class="pd"></td><td class="pd"></td><td></td><td class="x"><button id="clrAuto" class="del" title="Remove the auto pairs">Clear</button></td></tr>` : "");
  $("pairHint").textContent = `Click a spot on the photo and the same spot on the map to add pair ${st.nextN}.`;
  drawMapLines();
}
export function onRowsClick(e) {
  if (e.target.id === "clrAuto") { st.auto = []; renderRows(); draw(); setPose(); scheduleSave(); return; }
  if (e.target.dataset.del) return removePair(+e.target.dataset.del);
  const tr = e.target.closest("tr"); if (tr && tr.dataset.n && e.target.tagName !== "INPUT") select(+tr.dataset.n);
}
export function onRowsInput(e) {
  const p = st.pairs.find(q => q.n === +e.target.dataset.n); if (p) { p.label = e.target.value; scheduleSave(); }
}

export const payload = withAuto => ({ photo: st.photo.path, image_px: [st.photo.w, st.photo.h],
  ...(st.solve && st.solve.pose ? { camera: st.solve.pose, camera_source: st.solve.source || "solve" } : {}),
  labels: st.pairs.map(p => ({ label: p.label, x: p.x, y: p.y, lat: p.lat, lon: p.lon, h_ellipsoid_m: p.h ?? null }))
    .concat(withAuto ? st.auto.map(p => ({ label: p.label, x: p.x, y: p.y, lat: p.lat, lon: p.lon, h_ellipsoid_m: p.h ?? null, auto: true })) : []) });
// Autosave: every add, move, relabel and delete calls scheduleSave(). It snapshots the labels (and the region) now,
// so a photo or region switch within the delay cannot send the wrong photo's state, and sends the latest snapshot
// after 500 ms of quiet. flushSave() sends it at once (photo switch, region switch, page close). Sends are chained
// so they reach the server in order. A guessed camera is not saved with the pairs (it is only a start).
const SAVE_DELAY_MS = 500;
let pend = null, timer = 0, chain = Promise.resolve();
export function scheduleSave() {
  if (!st.photo || !st.img) return;
  const body = payload(true);
  if (body.camera_source === "guess") { delete body.camera; delete body.camera_source; }
  pend = { ...body, region: app.curRegion };
  clearTimeout(timer); setSaved("busy"); timer = setTimeout(() => flushSave(), SAVE_DELAY_MS);
}
export function flushSave(unloading) {
  clearTimeout(timer);
  const body = pend; pend = null;
  if (!body) return chain;
  chain = chain.then(async () => {
    try {
      const r = await fetch("/api/labels", { method: "POST", body: JSON.stringify(body), keepalive: !!unloading }).then(x => x.json());
      if (!r.ok) throw new Error(r.error || "the server refused");
      if (!pend) setSaved("ok");
    } catch (e) { setSaved("err", "Save failed: " + (e.message || e)); status("Save failed: " + (e.message || e)); }
  });
  return chain;
}
// Save the pairs, the auto pairs and the solved camera now. Solve and Auto register call it right after they succeed,
// so the camera survives a photo switch or a reload.
export async function saveNow(quiet) {
  if (!st.photo) return;
  clearTimeout(timer); pend = null; setSaved("busy");
  const r = await postJson("/api/labels", payload(true));
  if (r.ok) {
    setSaved("ok");
    status(quiet ? `Solved and saved (${r.n} labels${st.solve && st.solve.pose ? ", camera" : ""}).` : `Saved ${r.n} labels (${st.auto.length} auto).`);
  } else { setSaved("err", r.error); status("Save failed: " + r.error); }
}
export async function saveLabels(quiet) {
  try { await saveNow(quiet); } catch (e) { setSaved("err", e.message || String(e)); status("Save failed: " + (e.message || e)); }
}
