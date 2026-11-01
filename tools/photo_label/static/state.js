// Every piece of shared mutable state lives here and is imported, never re-declared elsewhere.
// The open region, photo, pairs, solved camera, projected buildings and the photo canvas view.
import { prefGet } from "./util.js";

export const st = { auto: [], photos: [], photo: null, img: null, pairs: [], pending: null, sel: null, nextN: 1,
             solve: null, bld: [], hoverId: null, hiId: null, view: { s: 1, ox: 0, oy: 0 }, mapMarkers: new Map(), footprints: null };
// Depth-view tab state (tabs: photo | pd photo depth | gd Google depth | df difference | or orientation).
export const DV_TABS = ["photo", "pd", "gd", "df", "or"];
export const dv = { tab: "photo", seq: 0, ver: 0, p: null, gc: new Map(), loading: false, gbusy: false, gerr: null, timer: 0, layer: null, chrome: "", src: "photo", view: "surf" };
{
  const t = prefGet("photoLabelTab"); if (DV_TABS.includes(t)) dv.tab = t;
  const s = prefGet("photoLabelOrSrc"); if (s === "photo" || s === "google") dv.src = s;
  const v = prefGet("photoLabelOrView"); if (["surf", "wall", "pitch"].includes(v)) dv.view = v;
}
// Loose page-wide state. curRegion: the region every fetch carries; regionSeq: lets a slow response from a
// region left behind be ignored; camPose: the solved or restored camera of the open photo; tiles3d: null =
// unknown, then true/false from /api/config; startView: {lat, lon, ground_m} of the region; satOn: satellite or
// OSM base map; autoBusy / guessSeq / guessBusy: Auto register and start-guess progress; renderLock: serialises
// renders on the offscreen Cesium viewer; drag: the canvas mouse drag in progress.
export const app = { curRegion: "", regionSeq: 0, camPose: null, tiles3d: null, startView: null, satOn: true,
              autoBusy: false, guessSeq: 0, guessBusy: false, renderLock: Promise.resolve(), drag: null };
// Footprints on the 2D map. layers: id -> Leaflet layer; hi: highlighted id; inView: ids recoloured to match the
// photo's outlines; group: the layers near the view; all: [layer, bounds] of every footprint; bounds: their extent.
export const fp = { layers: new Map(), hi: null, inView: new Set(), group: null, all: [], bounds: null };
// The 3D (Cesium) view: viewer, markers, whether it is showing.
export const ces = { viewer: null, ready: null, markers: new Map(), icons: new Map(), on: false, hfov: null };
// Our city model over the Google mesh.
export const mdl = { model: null, info: null, geoid: null, shader: null, busy: false };
// The page's mode and tool switches (Settings sheet). beginner: no tool shown; everything: all shown; custom: the
// switches in ui.tools. autoScreen: start the background screening of street-level frames on region load.
export const TOOL_KEYS = ["maps", "guesses", "pairs", "orient", "screen", "solve", "3d", "model"];
export const ui = { mode: "beginner", tools: {}, autoScreen: true };
export const toolOn = k => ui.mode === "everything" || (ui.mode === "custom" && !!ui.tools[k]);
{
  const m = prefGet("photoLabelMode"); if (["beginner", "custom", "everything"].includes(m)) ui.mode = m;
  try { const t = JSON.parse(prefGet("photoLabelTools", "{}")); for (const k of TOOL_KEYS) ui.tools[k] = !!t[k]; } catch (_) { /* bad json: all off */ }
  ui.autoScreen = prefGet("photoLabelAutoScreen", "1") !== "0";
}
