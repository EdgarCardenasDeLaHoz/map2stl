// The 2D Leaflet map: base layers, footprints, camera / sight-ray overlay, pair markers.
// The view comes from the region (/api/config), so the map has none until a region is loaded.
import { api, getJson } from "./api.js";
import { dropCesMarker, setCesMarker } from "./cesium3d.js";
import { draw } from "./draw.js";
import { offsetLL } from "./geo.js";
import { invalidate, renderRows, scheduleSave, select } from "./labels.js";
import { syncCtx } from "./chrome.js";
import { app, fp, st } from "./state.js";
import { $, colorOf, hue, status } from "./util.js";

export const map = L.map("map", { zoomControl: false });
L.control.zoom({ position: "topright" }).addTo(map);
export const sat = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
  { attribution: "Esri", maxZoom: 19 }).addTo(map);
export const osmTiles = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { attribution: "&copy; OpenStreetMap", maxZoom: 19 });
export const camLayer = L.layerGroup().addTo(map);
export const fpStyle = f => f.properties.name
  ? { color: "#ffd60a", weight: 2, fillOpacity: .15 } : { color: "#fff", weight: 0.8, fillOpacity: 0.03 };
export const rayLayer = L.layerGroup().addTo(map);  // "Map lines": fov wedge and sight rays
export const fpBase = f => fp.inView.has(f.properties.id)
  ? { color: `hsl(${hue(f.properties.id)},85%,55%)`, weight: 2, fillOpacity: .25 } : fpStyle(f);
// Map counterpart of the photo overlay, drawn from the solved camera; the checkbox hides all of it.
export function drawMapLines() {
  rayLayer.clearLayers();
  const old = fp.inView; fp.inView = new Set();
  for (const id of old) { const l = fp.layers.get(id); if (l && id !== fp.hi) l.setStyle(fpStyle(l.feature)); }
  const q = app.camPose;
  if (!q || !$("rayChk").checked) return;
  const pt = b => { const a = b * Math.PI / 180; return offsetLL(q.lat, q.lon, 1500 * Math.sin(a), 1500 * Math.cos(a)); };
  const h = q.heading_deg, hf = q.hfov_deg / 2;
  L.polygon([[q.lat, q.lon], pt(h - hf), pt(h), pt(h + hf)], { color: "#0a84ff", weight: 1, fillOpacity: .15, interactive: false }).addTo(rayLayer);
  for (const p of st.auto)
    L.polyline([[q.lat, q.lon], [p.lat, p.lon]], { color: "#00e5ff", weight: 1, opacity: .6, interactive: false }).addTo(rayLayer);
  for (const p of st.pairs) if (p.lat != null)
    L.polyline([[q.lat, q.lon], [p.lat, p.lon]], { color: colorOf(p.n), weight: 2, interactive: false }).addTo(rayLayer);
  for (const b of st.bld) {
    const l = fp.layers.get(b.id); if (!l) continue;
    fp.inView.add(b.id); if (b.id !== fp.hi) l.setStyle(fpBase(l.feature));
  }
}
export function mapHighlight(id) {
  if (fp.hi != null && fp.layers.has(fp.hi)) fp.layers.get(fp.hi).setStyle(fpBase(fp.layers.get(fp.hi).feature));
  fp.hi = id;
  const l = id == null ? null : fp.layers.get(id);
  if (l) { l.setStyle({ color: "#ff453a", weight: 3, fillOpacity: .35 }); l.bringToFront(); }
}
export const guessLayer = L.layerGroup().addTo(map);
export function toggleBase() {
  app.satOn = !app.satOn;
  if (app.satOn) { map.removeLayer(osmTiles); sat.addTo(map); } else { map.removeLayer(sat); osmTiles.addTo(map); }
  $("baseSat").classList.toggle("on", app.satOn); $("baseOsm").classList.toggle("on", !app.satOn); syncCtx();
}
// Where a footprint's drawn height comes from, in words: "31 m published (measured 148 m, withheld)".
export const SRC_WORDS = { osm_tag: "OSM height tag", osm_levels: "OSM levels x 3 m", published: "published",
                    measured_withheld: "published", default: "default" };
export function heightText(p) {                // p: footprint properties or a projected building
  const h = p.height_m != null ? p.height_m : p.height, src = p.height_source;
  if (h == null || !src) return h == null ? "" : `${Number(h).toFixed(0)} m`;
  let t = `${Number(h).toFixed(0)} m ${SRC_WORDS[src] || src}`;
  if (p.measured_m != null) t += src === "measured_withheld" ? `, measured ${p.measured_m.toFixed(0)} m (withheld)` : `, measured ${p.measured_m.toFixed(0)} m`;
  if (p.published_m != null && src !== "published" && src !== "measured_withheld") t += `, published ${p.published_m.toFixed(0)} m`;
  return t;
}
export function fpTip(p) {                     // map tooltip: the name and, when the pipeline knew it, the height source
  const known = p.height_source && p.height_source !== "default";
  return p.name || known ? [p.name, known ? heightText(p) : null].filter(Boolean).join(" - ") : null;
}
// One canvas for every footprint: as SVG each of a region's 40k buildings is a DOM node.
export const fpRenderer = L.canvas({ padding: 0.2, tolerance: 3 });
export async function openFootprints(seq) {
  const fc = await getJson(api("/api/footprints"));
  if (seq !== app.regionSeq) return;
  st.footprints = fc;
  const g = L.geoJSON(fc, {
    style: fpStyle,
    onEachFeature: (f, layer) => {
      fp.layers.set(f.properties.id, layer);
      const tip = fpTip(f.properties);
      if (tip) layer.bindTooltip(tip, { sticky: true });
      layer.on("click", () => { if (st.bld.length) { st.hiId = f.properties.id; draw(); } });
    },
    interactive: true,
    renderer: fpRenderer,
  });
  fp.all = g.getLayers().map(l => [l, l.getBounds()]);
  fp.group = L.layerGroup().addTo(map);
  fp.bounds = fc.features.length ? g.getBounds() : null;
  homeMap();
  fpWindow();
  status(fc.features.length + " footprints loaded.");
}
export async function loadFootprints(seq) {
  try { await openFootprints(seq); } catch (e) { status("Could not load the footprints: " + (e.message || e)); }
}
// Leaflet re-projects every layer on the map at each zoom, so only the footprints within a third
// a screen of the view are on it (Cartagena: 41k outlines made each zoom take ~0.5 s against
// ~30 ms without them). Below FP_MIN_ZOOM none are, unless the region is small.
export const FP_MIN_ZOOM = 15;
export function fpWindow() {
  if (!fp.group) return;
  const all = fp.all.length < 3000, on = all || map.getZoom() >= FP_MIN_ZOOM;
  const box = map.getBounds().pad(0.3);
  for (const [l, b] of fp.all) {
    const want = on && (all || box.intersects(b)), has = fp.group.hasLayer(l);
    if (want && !has) fp.group.addLayer(l); else if (!want && has) fp.group.removeLayer(l);
  }
}
// The 2D map's home: the region's start view, else the footprints' extent.
export function homeMap() {
  if (app.startView) map.setView([app.startView.lat, app.startView.lon], 15);
  else if (fp.bounds) map.fitBounds(fp.bounds);
}

export function pinIcon(n, pend) {
  return L.divIcon({ className: "", iconSize: [22, 22], iconAnchor: [11, 11],
    html: `<div class="pin${pend ? " pend" : ""}" style="background:${colorOf(n)}">${n}</div>` });
}
export function setMapMarker(p) {
  const old = st.mapMarkers.get(p.n); if (old) map.removeLayer(old);
  setCesMarker(p);
  if (p.lat == null) return;
  const m = L.marker([p.lat, p.lon], { icon: pinIcon(p.n, p === st.pending), draggable: true }).addTo(map);
  // A dragged 2D marker no longer sits at the 3D pick, so its height is dropped.
  m.on("dragend", () => { const ll = m.getLatLng(); p.lat = ll.lat; p.lon = ll.lng; p.h = null; p.hGround = null; setCesMarker(p); invalidate(); renderRows(); scheduleSave(); });
  m.on("click", () => select(p.n));
  st.mapMarkers.set(p.n, m);
}
export function dropMarker(n) {
  const m = st.mapMarkers.get(n); if (m) { map.removeLayer(m); st.mapMarkers.delete(n); }
  dropCesMarker(n);
}
export function showCam(q) {
  camLayer.clearLayers();
  L.circleMarker([q.lat, q.lon], { radius: 7, color: "#fff", fillColor: "#0a84ff", fillOpacity: 1 }).addTo(camLayer)
    .bindTooltip("camera");
  drawMapLines();
}
