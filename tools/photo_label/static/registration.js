// Solve from clicked pairs, and Auto register against renders of the Google 3D mesh (offscreen Cesium viewer).
import { api, postJson } from "./api.js";
import { setResult } from "./chrome.js";
import { loadCesium } from "./cesium3d.js";
import { dvRefresh } from "./depth-view.js";
import { draw, loadBuildings } from "./draw.js";
import { cesiumView, enuOffset, offsetLL, pinholeAxes } from "./geo.js";
import { payload, renderRows, saveLabels } from "./labels.js";
import { showCam } from "./map2d.js";
import { app, ces, st } from "./state.js";
import { autoRegisterStrip } from "./strip.js";
import { $, f32b64, status } from "./util.js";

export async function solveNow() {
  status("Solving...");
  const r = await postJson("/api/solve", payload(false));
  if (!r.ok) { status("Solve failed: " + r.error); return; }
  st.solve = { ...r, source: "solve" }; const q = r.pose; app.camPose = q; $("camViewBtn").disabled = false;
  showCam(q);
  setResult(`heading ${q.heading_deg.toFixed(1)} deg, hfov ${q.hfov_deg.toFixed(1)} deg, rms ${q.rms_px.toFixed(1)} px, ` +
    `sigma ${q.sigma_m == null ? "n/a" : q.sigma_m.toFixed(0) + " m"}, roll ${q.roll_deg.toFixed(1)} deg, f ${q.f_px.toFixed(0)} px` +
    (q.elev_m == null ? " (column solve, no terrain)" :
      `, pitch ${q.pitch_deg.toFixed(1)} deg, elev ${q.elev_m.toFixed(0)} m (+${(q.elev_m - q.ground_m).toFixed(0)} above ground), ` +
      `median ${q.median_px.toFixed(1)} px = ${q.median_m.toFixed(1)} m`) +
    (q.rejected.length ? `, rejected: ${q.rejected.join(", ")}` : ""));
  status("Solved."); renderRows(); draw(); dvRefresh(); await loadBuildings();
  saveLabels(true);
}
export async function onSolve() {
  try { await solveNow(); } catch (e) { status("Solve failed: " + (e.message || e)); }
}

// ---------- automatic registration against the 3D mesh ----------
// An offscreen second Cesium viewer renders the mesh from a candidate camera (colour + a depth grid from
// pickPosition); the server matches it to the photo and returns a better camera. Renders stay in memory.
export const REG_GRID_W = 112, REG_LONG_SIDE = 1600, REG_TILE_WAIT_MS = 90000;  // a cold Granada view takes ~30 s to settle
// Heading / FOV sweep when the start guess finds nothing: SWEEP_HEADINGS renders around the start
// position at SWEEP_HFOV (8 x 60 deg overlap by 15 deg), then the best heading at each of SWEEP_FOVS.
// Smaller, shorter renders: they only rank directions, the normal iterations refine after.
export const SWEEP_HEADINGS = 8, SWEEP_HFOV = 60, SWEEP_FOVS = [40, 25], SWEEP_LONG_SIDE = 1024, SWEEP_WAIT_MS = 30000;
export const SWEEP_IF_BELOW = 25;    // inliers from the start guess below which the sweep runs
export const MIN_SWEEP_SCORE = 6;    // fewer matches with a depth than this in every direction: nothing to iterate from
export async function ensureRenderViewer(rw, rh) {
  await loadCesium();
  if (!ces.rv) {
    const box = document.createElement("div"); box.style.cssText = "position:fixed;left:-30000px;top:0;";
    document.body.appendChild(box);
    const v = new Cesium.Viewer(box, {
      baseLayer: false, globe: false, geocoder: false, homeButton: false, sceneModePicker: false,
      baseLayerPicker: false, navigationHelpButton: false, animation: false, timeline: false,
      fullscreenButton: false, infoBox: false, selectionIndicator: false, vrButton: false,
      useDefaultRenderLoop: false, contextOptions: { webgl: { preserveDrawingBuffer: true } } });
    v.resolutionScale = 1 / (window.devicePixelRatio || 1);       // drawing buffer = CSS size = rw x rh
    v.scene.skyAtmosphere.show = false; v.scene.skyBox.show = false; v.scene.fog.enabled = false;
    v.scene.backgroundColor = Cesium.Color.BLACK;
    const ts = await Cesium.Cesium3DTileset.fromUrl("/tiles3d/v1/3dtiles/root.json");
    ts.maximumScreenSpaceError = 8;
    v.scene.primitives.add(ts);
    ces.rv = { v, box, ts };
  }
  const { v, box } = ces.rv;
  box.style.width = rw + "px"; box.style.height = rh + "px"; v.resize();
  return ces.rv;
}
export function disposeRenderViewer() {
  if (!ces.rv) return;
  try { ces.rv.v.destroy(); ces.rv.box.remove(); } catch (e) { /* already gone */ }
  ces.rv = null;
}
export function settleTiles(rv, ms) {
  return new Promise(res => {
    const t0 = performance.now(); let stable = 0;
    (function tick() {
      try { rv.v.render(); } catch (e) { return res(false); }   // viewer disposed (region switch): never hang the render lock
      stable = rv.ts.tilesLoaded ? stable + 1 : 0;
      if (stable >= 5) return res(true);
      if (performance.now() - t0 > ms) return res(false);
      setTimeout(tick, 60);
    })();
  });
}
// cam: PinholeCamera dict at the photo's size (elev_m orthometric); geoid_m is added for the mesh's ellipsoidal datum.
// One render at a time: Google depth (dvGoogle) and Auto register share the viewer, and a second
// setView while the first waits for tiles would move its camera.
export function renderAt(cam, geoid_m, gridW = REG_GRID_W, opts = {}) {
  const run = app.renderLock.then(() => renderNow(cam, geoid_m, gridW, opts));
  app.renderLock = run.catch(() => {});
  return run;
}
export async function renderNow(cam, geoid_m, gridW, { longSide = REG_LONG_SIDE, waitMs = REG_TILE_WAIT_MS } = {}) {
  const sc = Math.min(1, longSide / Math.max(cam.width, cam.height));
  const rw = Math.round(cam.width * sc), rh = Math.round(cam.height * sc);
  const rv = await ensureRenderViewer(rw, rh), v = rv.v;
  const cw = v.canvas.width, ch = v.canvas.height;                // what the browser really gave us
  const view = cesiumView(cam, cw, ch);
  v.camera.frustum.fov = view.fov;
  v.camera.setView({ destination: Cesium.Cartesian3.fromDegrees(cam.lon, cam.lat, cam.elev_m + geoid_m),
                     orientation: { heading: view.heading, pitch: view.pitch, roll: view.roll } });
  const loaded = await settleTiles(rv, waitMs);
  v.render();
  const image = v.canvas.toDataURL("image/jpeg", 0.92);
  const dw = gridW, dh = Math.max(8, Math.round(dw * ch / cw));
  const depth = new Float32Array(dw * dh).fill(NaN);
  const rcam = { ...cam, elev_m: cam.elev_m + geoid_m, width: cw, height: ch, f_px: cam.f_px * cw / cam.width };
  const [, , fwd] = pinholeAxes(rcam);
  for (let j = 0; j < dh; j++) for (let i = 0; i < dw; i++) {
    const c = v.scene.pickPosition(new Cesium.Cartesian2((i + .5) * cw / dw, (j + .5) * ch / dh));
    if (!c) continue;
    const g = Cesium.Cartographic.fromCartesian(c);
    const o = enuOffset(cam.lat, cam.lon, Cesium.Math.toDegrees(g.latitude), Cesium.Math.toDegrees(g.longitude));
    const z = o.e * fwd[0] + o.n * fwd[1] + (g.height - rcam.elev_m) * fwd[2];
    if (z > 0) depth[j * dw + i] = z;
  }
  return { image, depth: f32b64(depth), depth_w: dw, depth_h: dh, loaded };
}
export const PANO_CAM_HEIGHT_M = 2.5;                 // Street View camera above the road, as server.py
// Ellipsoidal ground height of the Google mesh at a point (render viewer's tileset), or null: the
// lowest of the point and two rings (4 and 8 m) round it, so a tree or awning over the car does not count.
export async function meshGround(lat, lon) {
  const rv = await ensureRenderViewer(REG_LONG_SIDE, REG_LONG_SIDE);
  let done = false;
  (function tick() { if (done) return; rv.v.render(); setTimeout(tick, 60); })();   // no render loop here: tiles load only on render()
  const pts = [Cesium.Cartographic.fromDegrees(lon, lat)];
  for (const r of [4, 8]) for (let a = 0; a < 360; a += 45) {
    const [la, lo] = offsetLL(lat, lon, r * Math.sin(a * Math.PI / 180), r * Math.cos(a * Math.PI / 180));
    pts.push(Cesium.Cartographic.fromDegrees(lo, la));
  }
  try {
    const got = await Promise.race([rv.v.scene.sampleHeightMostDetailed(pts),
                                    new Promise(res => setTimeout(() => res([]), 20000))]);
    const hs = got.map(c => c && c.height).filter(Number.isFinite);
    return hs.length ? Math.min(...hs) : null;
  } catch (_) { return null; } finally { done = true; }
}
export const isPanoStart = c => c.source === "pano" || c.source === "pano_anchor";   // known pose: no heading sweep
export async function autoRegister() {
  if (app.autoBusy || !st.photo) return;
  if (app.tiles3d === false) { status("Auto register needs the Google 3D tiles (no API key on the server)."); return; }
  app.autoBusy = true; $("autoBtn").disabled = true;
  const base = { width: st.photo.w, height: st.photo.h };
  try {
    if (st.photo.projection === "pano_cylindrical") { await autoRegisterStrip(); return; }
    let starts;
    if (app.camPose && app.camPose.elev_m != null) starts = [{ cam: { ...camPose, ...base }, geoid: st.geoid ?? 50,
      tag: st.solve && st.solve.source === "guess" && st.guesses ? `guess #${st.guesses.pick + 1}` : "current camera" }];
    else {
      status("Searching for a start pose from the skyline...");
      const r = await postJson("/api/register/candidates", { photo: st.photo.path, top_k: 3 });
      if (!r.ok || !r.candidates.length) { status("No start pose: " + (r.error || r.reason || "none found") + ". Solve with clicked pairs instead."); return; }
      starts = r.candidates.slice(0, 3).map((c, i) => ({ cam: { ...c, ...base }, geoid: r.geoid_m, tag: `candidate ${i + 1} (${c.source})` }));
    }
    const step = async (cam, geoid, rd) => postJson("/api/register/step", { photo: st.photo.path, camera: cam, geoid_m: geoid,
      user: st.pairs.filter(p => p.h != null).map(p => ({ x: p.x, y: p.y, lat: p.lat, lon: p.lon, h_orth: p.h - geoid })),
      render: rd.image, depth: rd.depth, depth_w: rd.depth_w, depth_h: rd.depth_h });
    let best = null;
    const iterate = async (cam, geoid, label) => {
      let prev = -1, regeoid = true;
      for (let it = 1; it <= 3; it++) {
        const tag = `${label}, iteration ${it}/3`;
        status(`Auto register: rendering ${tag}...`);
        const rd = await renderAt(cam, geoid);
        status(`Auto register: matching ${tag}${rd.loaded ? "" : " (tiles still loading)"}...`);
        const r = await step(cam, geoid, rd);
        if (!r.ok && regeoid && r.geoid_m != null && Math.abs(r.geoid_m - geoid) > 3) {
          // The start geoid was off (a region far from Granada's 50 m), so the render stood too high or
          // low to match. The server measured it from the render's depth: render again once with it.
          regeoid = false; geoid = r.geoid_m; it--; continue;
        }
        if (!r.ok) { status(`Auto register: ${tag}: ${r.reason || r.error}`); break; }
        status(`Auto register: ${tag}: ${r.n_inliers} inliers of ${r.n_matches}, median ${r.median_px.toFixed(1)} px, geoid ${r.geoid_m.toFixed(1)} m`);
        if (!best || r.n_inliers > best.r.n_inliers) best = { r, tag };
        cam = { ...r.camera, ...base }; geoid = r.geoid_m;
        if (r.n_inliers <= prev * 1.05) break;                   // no longer improving
        prev = r.n_inliers;
      }
    };
    for (const s of starts) {
      // a pano / pano_anchor start with no fitted elevation: DEM height can be a hill or a roof next to the car, stand on Google's ground
      // (a pose with elev_fitted, e.g. a drone sphere, keeps its elevation)
      if (isPanoStart(s.cam) && !s.cam.elev_fitted) {
        const g = await meshGround(s.cam.lat, s.cam.lon);
        if (g != null) s.cam = { ...s.cam, elev_m: g - s.geoid + PANO_CAM_HEIGHT_M };
      }
      await iterate(s.cam, s.geoid, s.tag);
    }
    // The guessed heading or FOV can be far off (a skyline search lines a sea horizon up anywhere):
    // look around from the first start's position, then iterate from the direction that matched best.
    const s0 = starts[0];
    if (!isPanoStart(s0.cam) && (!best || best.r.n_inliers < SWEEP_IF_BELOW)) {
      const sw = await sweepHeadings(s0.cam, s0.geoid, step);
      if (sw) await iterate(sw.cam, sw.geoid, `sweep (heading ${sw.cam.heading_deg.toFixed(0)}, hfov ${sw.hfov})`);
    }
    if (!best) { if (!/^Auto register: /.test($("status").textContent)) status("Auto register found no usable match."); return; }
    const { r } = best, q = r.camera;
    st.geoid = r.geoid_m;
    st.auto = r.pairs.map((p, i) => ({ n: "auto" + (i + 1), label: "auto" + (i + 1), x: p.x, y: p.y, lat: p.lat, lon: p.lon, h: p.h }));
    app.camPose = q; $("camViewBtn").disabled = false;
    st.solve = { pose: q, ticks: [], predicted_x: [], residuals_px: [], auto: true, source: "auto" };
    showCam(q);
    setResult(`auto register (${best.tag}): ${r.n_inliers} inliers of ${r.n_matches} matches, median ${r.median_px.toFixed(1)} px, ` +
      `geoid ${r.geoid_m.toFixed(1)} m, heading ${q.heading_deg.toFixed(1)}, pitch ${q.pitch_deg.toFixed(1)}, roll ${q.roll_deg.toFixed(1)}, ` +
      `hfov ${q.hfov_deg.toFixed(1)} deg, elev ${q.elev_m.toFixed(0)} m`);
    renderRows(); draw(); dvRefresh(); await loadBuildings();
    await saveLabels(true);
    status(`Auto register done: ${st.auto.length} auto pairs (cyan), camera set and saved.`);
  } catch (e) {
    status("Auto register failed: " + e.message);
  } finally {
    app.autoBusy = false; $("autoBtn").disabled = false;   // the viewer (and its loaded tiles) stays for the next photo
  }
}
// Renders SWEEP_HEADINGS directions at SWEEP_HFOV from cam0's position, then the best one at each of
// SWEEP_FOVS. Score: inliers when the step solved, else matches with a depth. Returns the best
// {cam, geoid, hfov} (the solved camera when that step solved), or null when nothing matched.
export async function sweepHeadings(cam0, geoid, step) {
  const withView = (heading, hfov) => ({ ...cam0, heading_deg: ((heading % 360) + 360) % 360, pitch_deg: Math.min(cam0.pitch_deg || 0, 0),
    f_px: cam0.width / 2 / Math.tan(hfov * Math.PI / 360), hfov_deg: hfov });
  const score = r => r.ok ? 1000 + r.n_inliers : (r.n_lifted || 0);
  let top = null;
  const tryCam = async (cam, hfov, what) => {
    status(`Auto register: sweep, ${what}...`);
    const rd = await renderAt(cam, geoid, REG_GRID_W, { longSide: SWEEP_LONG_SIDE, waitMs: SWEEP_WAIT_MS });
    const r = await step(cam, geoid, rd), sc = score(r);
    if (!top || sc > top.sc) top = { sc, hfov, cam: r.ok ? { ...r.camera, width: cam0.width, height: cam0.height } : cam, geoid: r.ok ? r.geoid_m : geoid, swept: cam };
  };
  for (let k = 0; k < SWEEP_HEADINGS; k++) {
    const h = cam0.heading_deg + k * 360 / SWEEP_HEADINGS;
    await tryCam(withView(h, SWEEP_HFOV), SWEEP_HFOV, `heading ${(((h % 360) + 360) % 360).toFixed(0)} (${k + 1}/${SWEEP_HEADINGS})`);
  }
  if (!top || top.sc < MIN_SWEEP_SCORE) return null;
  const h = top.swept.heading_deg;
  for (const f of SWEEP_FOVS) await tryCam(withView(h, f), f, `heading ${h.toFixed(0)}, hfov ${f}`);
  return top.sc < MIN_SWEEP_SCORE ? null : top;
}
