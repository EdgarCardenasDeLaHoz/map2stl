// Boot and event wiring: every handler is an imported function; this file only connects them to the page.
import { api, getJson } from "./api.js";
import { setMode3d, viewFromCamera } from "./cesium3d.js";
import { computeDepth, dvGoogle, setTab } from "./depth-view.js";
import { cv, draw, fit, layoutWide, loadBuildings, onCanvasDown, onCanvasLeave, onCanvasMove, onCanvasUp, onDragMove, onWheel, resize, zoomBy } from "./draw.js";
import { onGuessChk } from "./guesses.js";
import { flushSave, mapClick, onKeydown, onRowsClick, onRowsInput } from "./labels.js";
import { drawMapLines, fpWindow, homeMap, map, toggleBase } from "./map2d.js";
import { applyModelShow, loadModel, measureGeoid, modelMatrix } from "./model.js";
import { orMarks, orSet } from "./orient.js";
import { loadPhoto, onFlagBtn, onFlagChk, switchRegion } from "./photos.js";
import { autoRegister, onSolve } from "./registration.js";
import { app, ces, dv, mdl, st } from "./state.js";
import { initUi } from "./ui.js";
import { $, esc, prefGet, status } from "./util.js";

$("baseSat").onclick = () => { if (!app.satOn) toggleBase(); };
$("baseOsm").onclick = () => { if (app.satOn) toggleBase(); };
map.on("moveend", fpWindow);
map.on("click", e => mapClick(e.latlng));

$("mode2d").onclick = () => setMode3d(false);
$("mode3d").onclick = () => setMode3d(true);
$("camViewBtn").onclick = viewFromCamera;
for (const id of ["camUp", "camPitch"]) $(id).addEventListener("change", () => { if (app.camPose && ces.on) viewFromCamera(); });
$("mdlBtn").onclick = loadModel;
$("mdlShow").onchange = applyModelShow;
$("mdlAlpha").oninput = () => { if (mdl.shader) mdl.shader.setUniform("u_alpha", +$("mdlAlpha").value); };
$("mdlDz").addEventListener("input", () => { if (mdl.model) mdl.model.modelMatrix = modelMatrix(); });
$("mdlGeoid").onclick = measureGeoid;

// The panes can start at zero size (a hidden browser tab), so refit whenever one grows from
// nothing; after that a resize keeps the user's zoom.
new ResizeObserver(() => { const was = cv.width; resize(); if (was < 50) fit(); }).observe($("photoWrap"));
new ResizeObserver(([e]) => {
  const was = map.getSize().x; map.invalidateSize();
  if (was < 50 && e.contentRect.width >= 50 && app.curRegion) homeMap();
}).observe($("map"));
window.addEventListener("resize", () => { if (st.photo && document.body.classList.contains("widePhoto")) layoutWide(); });
$("fitBtn").onclick = fit;
$("zoomIn").onclick = () => zoomBy(1.4);
$("zoomOut").onclick = () => zoomBy(1 / 1.4);
window.addEventListener("pagehide", () => flushSave(true));

$("bldChk").onchange = draw;
$("hModeSel").onchange = loadBuildings;
$("rayChk").onchange = drawMapLines;
cv.addEventListener("mousemove", onCanvasMove);
cv.addEventListener("mouseleave", onCanvasLeave);
cv.addEventListener("mousedown", onCanvasDown);
window.addEventListener("mousemove", onDragMove);
window.addEventListener("mouseup", onCanvasUp);
cv.addEventListener("wheel", onWheel, { passive: false });

document.addEventListener("keydown", onKeydown);
$("rows").addEventListener("click", onRowsClick);
$("rows").addEventListener("input", onRowsInput);

$("solveBtn").onclick = onSolve;
$("autoBtn").onclick = autoRegister;
if (prefGet("photoLabelGuess") === "0") $("guessChk").checked = false;
$("guessChk").onchange = onGuessChk;

$("dTabs").onclick = e => { if (e.target.dataset.t) setTab(e.target.dataset.t); };
document.querySelectorAll("#dTabs button").forEach(b => b.classList.toggle("on", b.dataset.t === dv.tab));
$("dCompute").onclick = computeDepth;
$("gRender").onclick = dvGoogle;
$("oSrc").onclick = e => { if (e.target.dataset.v) orSet("src", e.target.dataset.v); };
$("oView").onclick = e => { if (e.target.dataset.v) orSet("view", e.target.dataset.v); };
orMarks();

$("photoSel").onchange = e => loadPhoto(e.target.value);
if (prefGet("photoLabelFlagged") === "1") $("flagChk").checked = true;
$("flagChk").onchange = onFlagChk;
$("flagBtn").onclick = onFlagBtn;
$("region").onchange = e => switchRegion(e.target.value);

initUi();
(async function startRegions() {
  let info;
  try { info = await getJson("/api/regions"); } catch (e) { status("Cannot reach the server: " + (e.message || e)); return; }
  $("region").innerHTML = info.regions.map(r => `<option value="${esc(r.id)}" data-label="${esc(r.label)}">${esc(r.label)} (${r.photos})</option>`).join("");
  let want = decodeURIComponent((location.hash.match(/region=([^&]*)/) || [])[1] || "");
  if (!want) want = prefGet("photoLabelRegion", "");
  switchRegion(info.regions.some(r => r.id === want) ? want : info.default);
})();
resize();
