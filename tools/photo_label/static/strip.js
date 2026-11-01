// Auto register for a pano strip (cylindrical projection): per-frame renders, then one cylindrical solve.
// Auto register on a pano strip (st.photo.projection "pano_cylindrical"): the server lists the seed's 12 frame
// cameras with the strip's pose (/api/register/candidates). Each frame pose is rendered from Google 3D (pinhole, as
// for any photo) and matched against the strip resampled to that frame (/api/register/step with `strip`), which
// returns inlier pairs in strip pixels. The pairs of all frames then go to the cylindrical solve (/api/solve).
import { api, postJson } from "./api.js";
import { setResult } from "./chrome.js";
import { dvRefresh } from "./depth-view.js";
import { draw, loadBuildings } from "./draw.js";
import { payload, renderRows, saveLabels } from "./labels.js";
import { showCam } from "./map2d.js";
import { PANO_CAM_HEIGHT_M, meshGround, renderAt } from "./registration.js";
import { app, st } from "./state.js";
import { $, status } from "./util.js";

export const STRIP_PAIRS_PER_FRAME = 6;
export async function autoRegisterStrip() {
  const r0 = await postJson("/api/register/candidates", { photo: st.photo.path, top_k: 1 });
  const strip = r0.ok && r0.candidates[0];
  if (!strip || !strip.frames || !strip.frames.length) { status("No frame cameras for this strip: " + (r0.error || r0.reason || "missing views.json") + "."); return; }
  let geoid = st.geoid ?? r0.geoid_m ?? 50, elev = strip.elev_m;
  if (!strip.elev_fitted) {
    const g = await meshGround(strip.lat, strip.lon);
    if (g != null) elev = g - geoid + PANO_CAM_HEIGHT_M;
  }
  const stripCam = { ...strip, elev_m: elev }, pairs = [], user = st.pairs.filter(p => p.h != null);
  let n = 0;
  for (const f of strip.frames) {
    n++;
    const cam = { ...f, elev_m: elev };
    status(`Auto register strip: rendering frame ${n}/${strip.frames.length} (heading ${f.heading_deg.toFixed(0)})...`);
    const rd = await renderAt(cam, geoid);
    const r = await postJson("/api/register/step", { photo: st.photo.path, camera: cam, strip: stripCam, geoid_m: geoid,
      user: [], render: rd.image, depth: rd.depth, depth_w: rd.depth_w, depth_h: rd.depth_h });
    if (!r.ok) { status(`Auto register strip: frame ${n}: ${r.reason || r.error}`); continue; }
    geoid = r.geoid_m;
    const ps = r.pairs, take = Math.min(STRIP_PAIRS_PER_FRAME, ps.length);
    for (let i = 0; i < take; i++) pairs.push(ps[Math.floor(i * ps.length / take)]);
  }
  if (pairs.length + user.length < 4) { status(`Auto register strip: only ${pairs.length} matches over the frames; solve with clicked pairs instead.`); return; }
  st.geoid = geoid;
  st.auto = pairs.map((p, i) => ({ n: "auto" + (i + 1), label: "auto" + (i + 1), x: p.x, y: p.y, lat: p.lat, lon: p.lon, h: p.h }));
  st.solve = { pose: stripCam, ticks: [], predicted_x: [], residuals_px: [], auto: true, source: "guess" };
  status("Auto register strip: fitting the strip camera...");
  const r = await postJson("/api/solve", payload(true));
  if (!r.ok) { status("Auto register strip: solve failed: " + r.error); return; }
  const q = r.pose;
  st.solve = { ...r, source: "auto" }; app.camPose = q; $("camViewBtn").disabled = false; showCam(q);
  setResult(`auto register (strip, ${pairs.length} pairs from ${strip.frames.length} frames): heading ${q.heading_deg.toFixed(1)}, ` +
    `pitch ${q.pitch_deg.toFixed(1)}, elev ${q.elev_m.toFixed(0)} m, median ${q.median_px.toFixed(1)} px = ${q.median_m.toFixed(1)} m` + (strip.weak ? " (weak seed pose: overhead)" : ""));
  renderRows(); draw(); dvRefresh(); await loadBuildings();
  await saveLabels(true);
  status(`Auto register strip done: ${st.auto.length} auto pairs (cyan), camera set and saved.`);
}
