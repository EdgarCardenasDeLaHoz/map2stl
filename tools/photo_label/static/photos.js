// The photo list and the open photo (load, flag, Show flagged), and region switching.
import { api, getJson, postJson } from "./api.js";
import { setPose, syncCtx } from "./chrome.js";
import { centre3d } from "./cesium3d.js";
import { dvRefresh, dvReset } from "./depth-view.js";
import { draw, fit, layoutWide, loadBuildings, resize } from "./draw.js";
import { clearGuesses, loadGuesses } from "./guesses.js";
import { flushSave, invalidate, renderRows } from "./labels.js";
import { dropMarker, homeMap, loadFootprints, map, setMapMarker, showCam } from "./map2d.js";
import { disposeModel } from "./model.js";
import { disposeRenderViewer } from "./registration.js";
import { screenPhotos } from "./screening.js";
import { app, ces, fp, st, toolOn, ui } from "./state.js";
import { $, esc, prefSet, status } from "./util.js";

export async function openPhoto(path) {
  flushSave();                                   // a pair edited a moment ago still goes to the photo it belongs to
  st.photo = st.photos.find(p => p.path === path);
  syncFlagBtn(); layoutWide(); syncCtx();
  for (const n of [...st.mapMarkers.keys(), ...ces.markers.keys()]) dropMarker(n);
  st.pairs = []; st.auto = []; st.pending = null; st.sel = null; st.nextN = 1; st.geoid = null; invalidate(); dvReset(); clearGuesses();
  const seq = app.regionSeq, ph = st.photo;
  const img = new Image();
  await new Promise((ok, bad) => { img.onload = ok; img.onerror = () => bad(new Error("could not load the image")); img.src = api("/photo/" + path.split("/").map(encodeURIComponent).join("/")); });
  const doc = await getJson(api("/api/labels?photo=" + encodeURIComponent(path)));
  if (seq !== app.regionSeq || st.photo !== ph) return;       // switched region or photo while loading
  st.img = img;
  for (const l of doc.labels || []) {
    if (l.lat == null || l.x == null) continue;
    if (l.auto) { st.auto.push({ n: "auto" + (st.auto.length + 1), label: l.label, x: l.x, y: l.y, lat: l.lat, lon: l.lon, h: l.h_ellipsoid_m ?? null }); continue; }
    const p = { n: st.nextN++, label: l.label, x: l.x, y: l.y, lat: l.lat, lon: l.lon, h: l.h_ellipsoid_m ?? null };
    st.pairs.push(p); setMapMarker(p);
  }
  invalidate();
  const q = doc.camera;                    // a saved camera: buildings reappear without solving again
  if (q && q.lat != null) {
    st.solve = { pose: q, ticks: [], predicted_x: [], residuals_px: [], restored: true, source: doc.camera_source || "solve" };
    app.camPose = q; $("camViewBtn").disabled = false; showCam(q); setPose();
    if (!ces.on) map.setView([q.lat, q.lon], 16); else centre3d(true);
    loadBuildings();
  }
  renderRows(); resize(); fit(); dvRefresh();
  status(`${st.pairs.length} saved pairs loaded` + (st.auto.length ? `, ${st.auto.length} auto pairs` : "") + (q ? ", saved camera restored." : "."));
  if (!(q && q.lat != null)) loadGuesses();
}
export async function loadPhoto(path) {
  try { await openPhoto(path); } catch (e) { status("Could not load " + path + ": " + (e.message || e)); }
}
// Flag the current photo as bad: the server keeps the flag and leaves it out of /api/photos;
// here it leaves the list and the next photo opens. The status line offers Undo.
export async function flagPhoto(path, bad) {
  let r = null;
  try { r = await postJson("/api/photo/flag", { photo: path, bad }); } catch (e) { r = { ok: false, reason: e.message }; }
  if (!r || !r.ok) { status("Could not flag: " + ((r && (r.reason || r.error)) || "server error")); return false; }
  return true;
}
// "Show flagged" lists flagged photos too (marked, flagBtn turns into Unflag) instead of hiding them.
export const showFlagged = () => $("flagChk").checked && toolOn("screen");
export const photoOpt = p => `<option value="${esc(p.path)}">${p.bad ? "⚑ flagged · " : ""}${esc(p.path)} (${p.w}x${p.h})</option>`;
export function fillPhotoSel() {
  // seed frames (seeds/seed_N/...) get one optgroup per seed folder; other photos stay flat
  const html = [], grp = p => p.path.startsWith("seeds/") ? p.path.slice(0, p.path.lastIndexOf("/")) : "";
  let cur = "";
  for (const p of st.photos) {
    const g = grp(p);
    if (g !== cur) { if (cur) html.push("</optgroup>"); if (g) html.push(`<optgroup label="${esc(g)}">`); cur = g; }
    html.push(photoOpt(p));
  }
  if (cur) html.push("</optgroup>");
  $("photoSel").innerHTML = html.join("");
  if (st.photo && st.photos.includes(st.photo)) $("photoSel").value = st.photo.path;
  syncCtx();
}
export function syncFlagBtn() {
  const bad = !!(st.photo && st.photo.bad);
  $("flagBtn").textContent = bad ? "⚑ Unflag" : "⚑ Flag bad";
}
export async function loadPhotoList(seq) {
  let list;
  try { list = await getJson(api("/api/photos" + (showFlagged() ? "?all=1" : ""))); }
  catch (e) { status("Could not list the photos: " + (e.message || e)); return null; }
  if (seq !== app.regionSeq) return null;
  st.photos = list;
  return list;
}
export async function onFlagChk() {
  prefSet("photoLabelFlagged", showFlagged() ? "1" : "0");
  const cur = st.photo && st.photo.path, list = await loadPhotoList(app.regionSeq);
  if (!list) return;
  st.photo = list.find(p => p.path === cur) || null;
  fillPhotoSel();
  if (!st.photo && list.length) { $("photoSel").value = list[0].path; loadPhoto(list[0].path); } else syncFlagBtn();
}
export async function onFlagBtn() {
  if (!st.photo) return;
  const item = st.photos.find(p => p.path === st.photo.path), at = st.photos.indexOf(item);
  if (item && showFlagged()) {                      // flagged photos stay listed: just toggle the mark
    if (!(await flagPhoto(item.path, !item.bad))) return;
    if (item.bad) delete item.bad; else item.bad = true;
    fillPhotoSel(); syncFlagBtn();
    status((item.bad ? "Flagged " : "Unflagged ") + item.path + ".");
    return;
  }
  if (!item || !(await flagPhoto(item.path, true))) return;
  st.photos.splice(at, 1);
  $("photoSel").querySelector(`option[value="${CSS.escape(item.path)}"]`)?.remove();
  const next = st.photos[Math.min(at, st.photos.length - 1)];
  if (next) { $("photoSel").value = next.path; await loadPhoto(next.path); } else status("No photos left.");
  $("status").innerHTML = "";
  $("status").append("Flagged " + item.path + " as bad. ");
  const undo = document.createElement("a"); undo.href = "#"; undo.textContent = "Undo";
  undo.onclick = async e => {
    e.preventDefault();
    if (!(await flagPhoto(item.path, false))) return;
    st.photos.splice(at, 0, item);
    const o = new Option(`${item.path} (${item.w}x${item.h})`, item.path);
    const ref = $("photoSel").options[at];
    (ref ? ref.parentNode : $("photoSel")).insertBefore(o, ref || null);
    $("photoSel").value = item.path; loadPhoto(item.path);
  };
  $("status").append(undo);
}

export async function loadConfig(seq) {
  try {
    const c = await getJson(api("/api/config"));
    if (seq !== app.regionSeq) return;
    app.tiles3d = !!c.tiles3d; app.startView = c.view || null;
  } catch (_) { app.tiles3d = false; }
}
// ---------- regions ----------
// Everything region-specific is dropped and reloaded: photos, footprints, view, the open photo and its
// pairs, any loaded model. app.regionSeq lets a slow response from the region left behind be ignored.
export function clearRegion() {
  for (const n of [...st.mapMarkers.keys(), ...ces.markers.keys()]) dropMarker(n);
  st.pairs = []; st.auto = []; st.pending = null; st.sel = null; st.nextN = 1;
  st.photo = null; st.img = null; st.photos = []; st.footprints = null; st.view = { s: 1, ox: 0, oy: 0 };
  clearGuesses(); invalidate(); renderRows(); draw(); dvReset(true);
  $("photoSel").innerHTML = "";
  if (fp.group) { map.removeLayer(fp.group); fp.group = null; }
  fp.all = [];
  fp.layers.clear(); fp.hi = null; fp.bounds = null;
  disposeModel(); disposeRenderViewer();    // its cached tiles belong to the region left behind
  ces.centredFor = null; setPose(); syncCtx();
}
export async function switchRegion(id) {
  flushSave();
  const seq = ++app.regionSeq;
  app.curRegion = id;
  history.replaceState(null, "", "#region=" + encodeURIComponent(id));
  prefSet("photoLabelRegion", id);
  if ($("region").value !== id) $("region").value = id;
  syncCtx();
  clearRegion();
  status("Loading " + id + "...");
  await loadConfig(seq);
  if (seq !== app.regionSeq) return;
  homeMap();
  if (ces.on) centre3d(true);
  loadFootprints(seq);
  const list = await loadPhotoList(seq);
  if (!list) return;
  fillPhotoSel();
  if (list.length) loadPhoto(list[0].path); else status("No photos in " + id + ".");
  if (ui.autoScreen) screenPhotos(seq);
}
