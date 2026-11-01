// The 3D map pane: Cesium viewer with Google Photorealistic 3D Tiles, pair markers, view from the solved camera.
import { offsetLL } from "./geo.js";
import { mapClick } from "./labels.js";
import { map } from "./map2d.js";
import { app, ces, st } from "./state.js";
import { $, colorOf, status } from "./util.js";

export const CESIUM_VER = "1.120.0";
export const CESIUM_BASE = `https://cdn.jsdelivr.net/npm/cesium@${CESIUM_VER}/Build/Cesium/`;
export function loadCesium() {
  if (window.Cesium) return Promise.resolve();
  window.CESIUM_BASE_URL = CESIUM_BASE;
  const css = document.createElement("link"); css.rel = "stylesheet"; css.href = CESIUM_BASE + "Widgets/widgets.css";
  document.head.appendChild(css);
  return new Promise((ok, bad) => {
    const sc = document.createElement("script"); sc.src = CESIUM_BASE + "Cesium.js";
    sc.onload = ok; sc.onerror = () => bad(new Error("could not load CesiumJS from the CDN")); document.head.appendChild(sc);
  });
}
export async function initCesium() {
  await loadCesium();
  const v = new Cesium.Viewer("cesium", {
    baseLayer: false, globe: false, geocoder: false, homeButton: false, sceneModePicker: false,
    baseLayerPicker: false, navigationHelpButton: false, animation: false, timeline: false,
    fullscreenButton: false, infoBox: false, selectionIndicator: false, vrButton: false,
  });
  ces.viewer = v;
  const ts = await Cesium.Cesium3DTileset.fromUrl("/tiles3d/v1/3dtiles/root.json", { showCreditsOnScreen: true });
  v.scene.primitives.add(ts);
  ces.tileset = ts;
  centre3d(false);
  // Left click without a app.drag (< 5 px) picks the surface.
  const h = new Cesium.ScreenSpaceEventHandler(v.scene.canvas); let down = null;
  h.setInputAction(e => { down = e.position.clone(); }, Cesium.ScreenSpaceEventType.LEFT_DOWN);
  h.setInputAction(e => {
    if (!down || Cesium.Cartesian2.distance(down, e.position) >= 5) { down = null; return; }
    down = null; pick3d(e.position);
  }, Cesium.ScreenSpaceEventType.LEFT_UP);
  v.scene.preRender.addEventListener(applyFov);
  new ResizeObserver(() => v.resize()).observe($("cesium"));
}
// Look at a point from the south and above (heading north). The region's start view is 1.2 km back and
// 350 m up; a photo's saved camera is looked at from 500 m back and 300 m up. Heights are ellipsoidal
// and only roughly above the ground, which is enough for an overview.
export function lookFrom(lat, lon, ground, back, up, pitchDeg, fly) {
  if (!ces.viewer) return;
  const o = { destination: Cesium.Cartesian3.fromDegrees(lon, offsetLL(lat, lon, 0, -back)[0], ground + up),
              orientation: { heading: 0, pitch: Cesium.Math.toRadians(pitchDeg), roll: 0 } };
  if (fly) ces.viewer.camera.flyTo({ ...o, duration: 1.5 }); else ces.viewer.camera.setView(o);
}
// Centre the 3D view: on the open photo's camera when it has one, else on the region.
export function centre3d(fly) {
  const q = app.camPose, sv = app.startView;
  if (q) lookFrom(q.lat, q.lon, q.ground_m ?? (sv ? sv.ground_m : 650), 500, 300, -30, fly);
  else if (sv) lookFrom(sv.lat, sv.lon, sv.ground_m, 1220, 350, -22, fly);
  ces.centredFor = q ? st.photo && st.photo.path : null;
}
export function pick3d(pos) {
  const v = ces.viewer; if (!v || !st.img) return;
  const c = v.scene.pickPositionSupported ? v.scene.pickPosition(pos) : undefined;
  if (!c) { status("No 3D surface under that click."); return; }
  const g = Cesium.Cartographic.fromCartesian(c);
  mapClick({ lat: Cesium.Math.toDegrees(g.latitude), lng: Cesium.Math.toDegrees(g.longitude) }, g.height);
}
// Cesium's frustum.fov is the horizontal angle on a wide canvas and the vertical one on a tall canvas.
export function applyFov() {
  if (ces.hfov == null) return;
  const v = ces.viewer, w = v.canvas.clientWidth, h = v.canvas.clientHeight; if (!w || !h) return;
  const a = w / h, hf = Cesium.Math.toRadians(ces.hfov);
  v.camera.frustum.fov = a >= 1 ? hf : 2 * Math.atan(Math.tan(hf / 2) / a);
}
export function cesIcon(n, pend) {
  const key = n + (pend ? "p" : "");
  if (!ces.icons.has(key)) {
    const c = document.createElement("canvas"); c.width = c.height = 26; const g = c.getContext("2d");
    g.beginPath(); g.arc(13, 13, 11, 0, 7); g.fillStyle = colorOf(n); g.fill();
    g.lineWidth = 2; g.strokeStyle = "#fff"; if (pend) g.setLineDash([3, 3]); g.stroke();
    g.fillStyle = "#fff"; g.font = "bold 12px system-ui"; g.textAlign = "center"; g.textBaseline = "middle"; g.fillText(n, 13, 14);
    ces.icons.set(key, c.toDataURL());
  }
  return ces.icons.get(key);
}
export function setCesMarker(p) {
  dropCesMarker(p.n);
  if (!ces.viewer || p.lat == null) return;
  const h = p.h ?? p.hGround;
  if (h == null) { groundUnder(p); return; }
  ces.markers.set(p.n, ces.viewer.entities.add({
    position: Cesium.Cartesian3.fromDegrees(p.lon, p.lat, h),
    billboard: { image: cesIcon(p.n, p === st.pending), disableDepthTestDistance: Number.POSITIVE_INFINITY,
      verticalOrigin: Cesium.VerticalOrigin.CENTER },
  }));
}
// A 2D-only pair has no height. Clamping to the tiles does not work without a globe (the pin
// stays at height 0, hundreds of metres underground, and slides as the view orbits), so sample
// the surface under it once and keep that as a display height; it is not saved with the label.
export async function groundUnder(p) {
  if (p.hGroundBusy) return; p.hGroundBusy = true;
  try {
    const [c] = await ces.viewer.scene.sampleHeightMostDetailed([Cesium.Cartographic.fromDegrees(p.lon, p.lat)]);
    p.hGround = c && Number.isFinite(c.height) ? c.height : null;
  } catch (_) { p.hGround = null; }
  p.hGroundBusy = false;
  if (p.hGround != null && (st.pairs.includes(p) || st.pending === p)) setCesMarker(p);
}
export function dropCesMarker(n) {
  const e = ces.markers.get(n); if (e && ces.viewer) ces.viewer.entities.remove(e);
  ces.markers.delete(n);
}
export function syncCesMarkers() { for (const p of st.pairs.concat(st.pending ? [st.pending] : [])) setCesMarker(p); }

export async function setMode3d(on) {
  if (on && !ces.on) {
    if (app.tiles3d === false) { status("3D needs GOOGLE_MAPS_API_KEY"); return; }
    $("mode3d").disabled = $("mode2d").disabled = true; status("Loading the 3D view...");
    try {
      if (!ces.ready) ces.ready = initCesium();
      await ces.ready;
    } catch (err) {
      ces.ready = null; $("mode3d").disabled = $("mode2d").disabled = false;
      status("3D failed: " + (err && err.message || err)); return;
    }
    $("mode3d").disabled = $("mode2d").disabled = false;
  }
  ces.on = on;
  document.body.classList.toggle("mode3d", on);
  $("cesium").classList.toggle("hid", !on); $("map").classList.toggle("hid", on);
  $("mode3d").classList.toggle("on", on); $("mode2d").classList.toggle("on", !on);
  if (on) {
    // A camera restored or solved since the view was last centred pulls it over; otherwise the user's orbit stays.
    if (app.camPose && ces.centredFor !== (st.photo && st.photo.path)) centre3d(false);
    syncCesMarkers(); ces.viewer.resize(); status("3D: drag to orbit, click (no app.drag) to place a point."); }
  else { map.invalidateSize(); }
}
// Fly the 3D camera to the solved pose so the view can be compared with the photo.
export async function viewFromCamera() {
  const v = ces.viewer, q = app.camPose; if (!v || !q) return;
  status("Finding the surface under the camera...");
  const carto = Cesium.Cartographic.fromDegrees(q.lon, q.lat);
  let ground = null;
  try {
    const r = await v.scene.sampleHeightMostDetailed([carto]); ground = r[0] && r[0].height;
  } catch (e) { /* tiles not ready yet: fall back below */ }
  if (ground == null || !isFinite(ground)) { ground = v.scene.sampleHeight(carto); }
  const miss = ground == null || !isFinite(ground);
  // Granada's centre is about 680 m orthometric + 50 m geoid undulation; elsewhere the DEM median.
  if (miss) ground = app.startView ? app.startView.ground_m : 750;
  ces.hfov = q.hfov_deg; applyFov();
  v.camera.flyTo({
    destination: Cesium.Cartesian3.fromDegrees(q.lon, q.lat, ground + (+$("camUp").value || 0)),
    // The solver's positive roll lifts the right side; Cesium's positive roll lowers it.
    orientation: { heading: Cesium.Math.toRadians(q.heading_deg), pitch: Cesium.Math.toRadians(+$("camPitch").value || 0),
                   roll: Cesium.Math.toRadians(-(q.roll_deg || 0)) },
    duration: 1.5,
  });
  status(miss ? "No surface found under the camera; used 750 m ellipsoidal as ground." : `Camera ground ${ground.toFixed(1)} m (ellipsoidal).`);
}
