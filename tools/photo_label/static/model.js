// Our city model (glb from the server) over the Google mesh in the 3D view.
// ---------- our city model over the Google mesh ----------
// The server sends the model in east-north-up metres at info.origin with orthometric heights
// (GET /api/model/info, /api/model.glb). It is lifted by the geoid offset, measured here from
// the live Google mesh and kept in memory only (nothing measured from the tiles goes anywhere),
// plus the dz nudge.
// Remove the loaded model (a region switch): the next Model click builds the new region's.
import { api, getJson } from "./api.js";
import { hit } from "./draw.js";
import { app, ces, mdl } from "./state.js";
import { $, status } from "./util.js";

export function disposeModel() {
  if (mdl.model && ces.viewer) { try { ces.viewer.scene.primitives.remove(mdl.model); } catch (_) { /* gone already */ } }
  mdl.model = mdl.info = mdl.geoid = mdl.shader = null;
  for (const id of ["mdlShow", "mdlAlpha", "mdlDz", "mdlGeoid"]) $(id).disabled = true;
  $("mdlShow").value = "both"; $("mdlGeoid").textContent = "geoid ?";
  if (ces.tileset) ces.tileset.show = true;
}
export const GEOID_PCT = 0.2;   // like register.GEOID_PERCENTILE: ground points, not roofs or trees
export function modelMatrix() {
  const o = mdl.info.origin, up = (mdl.geoid ?? 0) + (+$("mdlDz").value || 0);
  return Cesium.Transforms.eastNorthUpToFixedFrame(Cesium.Cartesian3.fromDegrees(o.lon, o.lat, up));
}
// No normals in the glb: flat shading from screen-space derivatives, headlight style.
export function modelShader() {
  return new Cesium.CustomShader({
    mode: Cesium.CustomShaderMode.MODIFY_MATERIAL,
    lightingModel: Cesium.LightingModel.UNLIT,
    translucencyMode: Cesium.CustomShaderTranslucencyMode.TRANSLUCENT,
    uniforms: { u_alpha: { type: Cesium.UniformType.FLOAT, value: +$("mdlAlpha").value } },
    fragmentShaderText: `
      void fragmentMain(FragmentInput fsInput, inout czm_modelMaterial material) {
        vec3 p = fsInput.attributes.positionEC;
        vec3 n = normalize(cross(dFdx(p), dFdy(p)));
        material.diffuse *= 0.35 + 0.65 * abs(dot(n, normalize(-p)));
        material.alpha = u_alpha;
      }`,
  });
}
export async function modelInfo(rebuild) {
  for (let first = true; ; first = false) {
    const info = await getJson(api("/api/model/info") + (rebuild && first ? "&rebuild=1" : ""));
    if (info.status !== "building") return info;
    status("Building our city model on the server (the first time takes about 2 minutes)...");
    await new Promise(ok => setTimeout(ok, 3000));
  }
}
export async function measureGeoid() {
  const g = mdl.info.ground || [];
  if (!g.length) { status("No ground points for the geoid offset; set dz by hand."); return; }
  $("mdlGeoid").disabled = true; $("mdlGeoid").textContent = "geoid ...";
  try {
    const pts = g.map(([lon, lat]) => Cesium.Cartographic.fromDegrees(lon, lat));
    const got = await ces.viewer.scene.sampleHeightMostDetailed(pts, mdl.model ? [mdl.model] : []);
    const d = got.map((c, i) => c && Number.isFinite(c.height) ? c.height - g[i][2] : NaN)
      .filter(Number.isFinite).sort((a, b) => a - b);
    if (d.length < 5) { status(`Only ${d.length} ground points hit the Google mesh; set dz by hand.`); return; }
    mdl.geoid = d[Math.floor(GEOID_PCT * (d.length - 1))];
    if (mdl.model) mdl.model.modelMatrix = modelMatrix();
    status(`Geoid offset ${mdl.geoid.toFixed(1)} m from ${d.length} ground points (Google - DEM, 20th percentile).`);
  } catch (err) {
    status("Geoid measurement failed: " + (err && err.message || err));
  } finally {
    $("mdlGeoid").disabled = !mdl.model;
    $("mdlGeoid").textContent = mdl.geoid == null ? "geoid ?" : `geoid ${mdl.geoid.toFixed(1)} m`;
  }
}
export function applyModelShow() {
  const v = $("mdlShow").value;
  if (mdl.model) mdl.model.show = v !== "google";
  if (ces.tileset) ces.tileset.show = v !== "model";
}
export async function loadModel() {
  if (mdl.busy || !ces.viewer) return;
  if (mdl.model) { ces.viewer.flyTo(mdl.model); return; }
  mdl.busy = true; $("mdlBtn").disabled = true;
  const seq = app.regionSeq;
  try {
    const info = await modelInfo(true);
    if (seq !== app.regionSeq) return;
    if (info.status !== "ready") throw new Error(info.error || info.status);
    mdl.info = info;
    status("Loading our city model...");
    mdl.shader = modelShader();
    mdl.model = await Cesium.Model.fromGltfAsync({
      url: api("/api/model.glb"), modelMatrix: modelMatrix(), customShader: mdl.shader,
      upAxis: Cesium.Axis.Z, forwardAxis: Cesium.Axis.X,   // vertices are already (E, N, U)
      allowPicking: false, backFaceCulling: false,
    });
    if (seq !== app.regionSeq) { mdl.model = null; return; }   // the region changed while it loaded
    ces.viewer.scene.primitives.add(mdl.model);
    for (const id of ["mdlShow", "mdlAlpha", "mdlDz"]) $(id).disabled = false;
    applyModelShow();
    await measureGeoid();
    const mb = (info.glb_bytes / 1e6).toFixed(1);
    status(`Model: ${info.source}, ${info.faces} faces, ${mb} MB; geoid ${mdl.geoid == null ? "not measured" : mdl.geoid.toFixed(1) + " m"}.`);
  } catch (err) {
    status("Model failed: " + (err && err.message || err));
  } finally {
    mdl.busy = false; $("mdlBtn").disabled = false;
  }
}
