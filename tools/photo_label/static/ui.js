// Mode (Beginner / Custom / Everything), the tool switches, the Settings sheet and the region / photo popover.
// Beginner shows the core job; each tool switch adds its own controls to a card (elements marked data-need="<tool>"),
// so switching a tool never moves the Beginner layout. Mode and switches persist through prefSet.
import { setMode3d } from "./cesium3d.js";
import { setTab } from "./depth-view.js";
import { draw, loadBuildings } from "./draw.js";
import { drawGuesses } from "./guesses.js";
import { drawMapLines } from "./map2d.js";
import { onFlagChk } from "./photos.js";
import { screenPhotos } from "./screening.js";
import { TOOL_KEYS, app, ces, dv, toolOn, ui } from "./state.js";
import { $, esc, prefSet } from "./util.js";

// Tool rows: name, one line of help, icon glyph and its colour. Beginner already has building outlines, Flag bad,
// the depth tabs and the camera readout, so those have no switch.
const TOOLS = {
  maps: ["Map overlays", "Hide building outlines, choose heights, sight lines", "▦", "#0a84ff"],
  guesses: ["Start guesses", "Numbered camera guesses on the map", "◎", "#30d158"],
  pairs: ["Pair details", "Coordinates, residual in m and solved height per pair", "☰", "#ff9f0a"],
  orient: ["Orientation", "Walls, roofs and ground drawn on the photo", "⌂", "#bf5af2", true],
  screen: ["Screening and flags", "Show flagged photos in the list", "⚑", "#ff453a"],
  solve: ["Solve details", "Field of view, roll, error in px and m", "▤", "#8e8e93", true],
  "3d": ["3D map", "Google 3D map; fly to the photo camera", "◍", "#0a84ff", true],
  model: ["Model view", "Our city model over the Google mesh", "▣", "#ff375f", true],
};
const GROUPS = [
  ["See the match", "Overlays that help you check the match", ["maps", "guesses", "pairs"]],
  ["Understand the scene", "Extra views of the photo", ["orient"]],
  ["Manage photos", "Flagging and screening of the photo list", ["screen", "auto"]],
  ["Advanced", "Numbers and 3D tools for experts", ["solve", "3d", "model"]],
];

const effective = () => Object.fromEntries(TOOL_KEYS.map(k => [k, toolOn(k)]));
function persist() { prefSet("photoLabelMode", ui.mode); prefSet("photoLabelTools", JSON.stringify(ui.tools)); }

export function setMode(m) {
  if (m === "custom" && ui.mode !== "custom" && !TOOL_KEYS.some(k => ui.tools[k])) ui.tools = effective();
  ui.mode = m; persist(); applyUi();
}
export function setTool(k, on) {
  if (ui.mode !== "custom") ui.tools = effective();       // start from what is showing, then change the one switch
  ui.tools[k] = on;
  ui.mode = TOOL_KEYS.some(x => ui.tools[x]) ? "custom" : "beginner";
  persist(); applyUi();
}
function resetGroup(gi) {
  const keys = GROUPS[gi][2];
  const t = ui.mode === "custom" ? { ...ui.tools } : effective();
  for (const k of keys) { if (k === "auto") setAutoScreen(true); else t[k] = false; }
  ui.tools = t; ui.mode = TOOL_KEYS.some(x => t[x]) ? "custom" : "beginner";
  persist(); applyUi();
}
export function setAutoScreen(on) {
  const was = ui.autoScreen; ui.autoScreen = on; prefSet("photoLabelAutoScreen", on ? "1" : "0");
  if (on && !was && app.curRegion) screenPhotos(app.regionSeq);       // switched on: screen this region now
  syncSheet();
}

let prevScreen = null;
export function applyUi() {
  document.querySelectorAll("[data-need]").forEach(e => { e.hidden = !e.dataset.need.split(" ").some(toolOn); });
  document.body.classList.toggle("t-pairs", toolOn("pairs"));
  $("mapTL").hidden = ![...$("mapTL").children].some(c => !c.hidden);
  // a tool switched off puts its controls back to the Beginner defaults, so a hidden checkbox cannot keep an overlay changed
  if (!toolOn("maps")) {
    const hm = $("hModeSel").value !== "published";
    if (!$("bldChk").checked || !$("rayChk").checked || hm) {
      $("bldChk").checked = true; $("rayChk").checked = true; $("hModeSel").value = "published";
      draw(); drawMapLines(); if (hm) loadBuildings();
    }
  }
  if (!toolOn("orient") && dv.tab === "or") setTab("photo");
  if (ces.on && !toolOn("3d") && !toolOn("model")) setMode3d(false);
  drawGuesses();
  const sc = toolOn("screen");
  if (prevScreen !== null && prevScreen !== sc && $("flagChk").checked && app.curRegion) onFlagChk();
  prevScreen = sc;
  syncSheet();
}
function syncSheet() {
  document.querySelectorAll("#sheet [data-mode]").forEach(b => b.classList.toggle("on", b.dataset.mode === ui.mode));
  document.querySelectorAll("#sheet input[data-tool]").forEach(i => { i.checked = toolOn(i.dataset.tool); });
  const a = document.querySelector("#sheet input[data-auto]"); if (a) a.checked = ui.autoScreen;
}

function buildSheet() {
  const sw = attr => `<label class="sw2"><input type="checkbox" ${attr}><i></i></label>`;
  const row = (title, desc, ico, col, adv, ctl, extra = "") =>
    `<div class="row ${extra}" data-s="${esc((title + " " + desc).toLowerCase())}"><span class="ico" style="background:${col}">${ico}</span>` +
    `<div class="rt"><b>${esc(title)}${adv ? ' <em class="adv">ADVANCED</em>' : ""}</b><span>${esc(desc)}</span></div>${ctl}</div>`;
  let h = `<div class="gcap" data-g="mode"><span>Mode</span></div><div class="list" data-g="mode">` +
    row("Mode", "Switching any tool on sets Custom", "◧", "#5e5ce6", false,
      `<span class="seg"><button data-mode="beginner">Beginner</button><button data-mode="custom">Custom</button><button data-mode="everything">Everything</button></span>`) + `</div>`;
  GROUPS.forEach(([name, tip, keys], gi) => {
    h += `<div class="gcap" data-g="${gi}"><span title="${esc(tip)}">${esc(name)}</span><a href="#" data-reset="${gi}">Reset</a></div><div class="list" data-g="${gi}">`;
    for (const k of keys) {
      if (k === "auto") h += row("Auto-screen photos", "Flag street-level frames and panoramas in the background when a region loads", "✓", "#30b0c7", false, sw("data-auto"), "sub");
      else { const [t, d, i, c, adv] = TOOLS[k]; h += row(t, d, i, c, adv, sw(`data-tool="${k}"`)); }
    }
    h += `</div>`;
  });
  $("sheetBody").innerHTML = h;
}
export function openSheet(open = true) {
  $("sheet").hidden = !open; $("scrim").hidden = !open;
  if (open) { $("sheetSearch").value = ""; filterSheet(); $("sheetSearch").focus({ preventScroll: true }); } else $("gearBtn").focus();
}
function filterSheet() {
  const q = $("sheetSearch").value.trim().toLowerCase();
  document.querySelectorAll("#sheet .row").forEach(r => { r.hidden = !!q && !r.dataset.s.includes(q); });
  document.querySelectorAll("#sheet .list").forEach(l => {
    const any = [...l.children].some(r => !r.hidden);
    l.hidden = !any; document.querySelector(`#sheet .gcap[data-g="${l.dataset.g}"]`).hidden = !any;
  });
}
function ctxOpen(open) { $("ctxPop").hidden = !open; $("ctxBtn").setAttribute("aria-expanded", String(open)); }

export function initUi() {
  buildSheet();
  $("gearBtn").onclick = () => openSheet(true);
  $("sheetClose").onclick = $("scrim").onclick = () => openSheet(false);
  $("sheetSearch").oninput = filterSheet;
  $("sheet").addEventListener("click", e => {
    const m = e.target.closest("[data-mode]"); if (m) return setMode(m.dataset.mode);
    const r = e.target.closest("[data-reset]"); if (r) { e.preventDefault(); resetGroup(+r.dataset.reset); }
  });
  $("sheet").addEventListener("change", e => {
    if (e.target.dataset.tool) setTool(e.target.dataset.tool, e.target.checked);
    else if ("auto" in e.target.dataset) setAutoScreen(e.target.checked);
  });
  $("ctxBtn").onclick = () => ctxOpen($("ctxPop").hidden);
  $("photoSel").addEventListener("change", () => ctxOpen(false));
  $("region").addEventListener("change", () => ctxOpen(false));
  document.addEventListener("click", e => { if (!e.target.closest("#ctx")) ctxOpen(false); });
  document.addEventListener("keydown", e => {
    if (e.key !== "Escape") return;
    if (!$("sheet").hidden) { openSheet(false); e.stopPropagation(); } else if (!$("ctxPop").hidden) ctxOpen(false);
  }, true);
  // a page address can open the sheet or force a mode (used for screenshots); it is not saved
  const h = location.hash;
  const m = (h.match(/mode=(beginner|custom|everything)/) || [])[1]; if (m) ui.mode = m;
  applyUi();
  if (/settings/.test(h)) openSheet(true);
}
