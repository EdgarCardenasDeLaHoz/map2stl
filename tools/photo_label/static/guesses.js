// ---------- start guesses: where we think the photo was taken (server candidates), toggled on the map ----------
// st.guesses = { list, hints, geoid, pick }. A guess is applied as the current camera but never saved until the user solves / saves.
import { api, postJson } from "./api.js";
import { setResult } from "./chrome.js";
import { dvRefresh } from "./depth-view.js";
import { draw, loadBuildings } from "./draw.js";
import { offsetLL } from "./geo.js";
import { renderRows } from "./labels.js";
import { guessLayer, map, showCam } from "./map2d.js";
import { app, ces, st, toolOn } from "./state.js";
import { $, prefSet, status } from "./util.js";

export const GUESS_COLORS = ["#34c759", "#0a84ff", "#ff9f0a", "#bf5af2", "#ff453a", "#00c7be"];
export function clearGuesses() { app.guessSeq++; app.guessBusy = false; st.guesses = null; guessLayer.clearLayers(); }
export function wedgePts(q, len) {
  const h = q.heading_deg, hf = (q.hfov_deg || 60) / 2, pts = [[q.lat, q.lon]];
  for (let k = 0; k <= 8; k++) { const a = (h - hf + 2 * hf * k / 8) * Math.PI / 180;
    pts.push(offsetLL(q.lat, q.lon, len * Math.sin(a), len * Math.cos(a))); }
  return pts;
}
export const guessText = (c, i) => `#${i + 1} ${c.label || c.source} — heading ${c.heading_deg.toFixed(0)}°, pitch ${(c.pitch_deg ?? 0).toFixed(0)}°, elev ${(c.elev_m ?? 0).toFixed(0)} m, score ${(+c.score).toFixed(2)}`;
export function drawGuesses() {
  guessLayer.clearLayers();
  const g = st.guesses; if (!g || !$("guessChk").checked || !toolOn("guesses")) return;
  const icon = (html, sz) => L.divIcon({ className: "", html, iconSize: [sz, sz], iconAnchor: [sz / 2, sz / 2] });
  const h = g.hints || {};
  const tip = (m, t) => m.bindTooltip(t);
  if (h.gps) tip(L.marker(h.gps, { icon: icon('<div style="font:16px/16px sans-serif;color:#ffd60a;text-shadow:0 0 3px #000">◆</div>', 16), interactive: true, keyboard: false }), "Photo GPS").addTo(guessLayer);
  if (h.viewpoint) tip(L.marker([h.viewpoint.lat, h.viewpoint.lon], { icon: icon('<div style="font:18px/18px sans-serif;color:#ffd60a;text-shadow:0 0 3px #000">★</div>', 18), keyboard: false }), "Viewpoint: " + h.viewpoint.name).addTo(guessLayer);
  if (h.subject) {
    tip(L.marker([h.subject.lat, h.subject.lon], { icon: icon('<div style="font:18px/18px sans-serif;color:#ff453a;text-shadow:0 0 3px #000">◎</div>', 18), keyboard: false }), "Subject: " + h.subject.name).addTo(guessLayer);
    if (h.viewpoint) L.polyline([[h.viewpoint.lat, h.viewpoint.lon], [h.subject.lat, h.subject.lon]], { color: "#ff453a", weight: 1.5, dashArray: "5 5", interactive: false }).addTo(guessLayer);
  }
  g.list.forEach((c, i) => {
    const col = GUESS_COLORS[i % GUESS_COLORS.length], on = i === g.pick, t = guessText(c, i);
    L.polygon(wedgePts(c, 1200), { color: col, weight: on ? 2 : 1, fillColor: col, fillOpacity: on ? .22 : .1, bubblingMouseEvents: false })
      .on("click", () => applyGuess(i)).bindTooltip(t, { sticky: true }).addTo(guessLayer);
    if (on) {
      const m = L.marker([c.lat, c.lon], { draggable: true, zIndexOffset: 1000, keyboard: false, icon: icon(
        `<div style="width:26px;height:26px;border-radius:50%;background:${col};border:3px solid #fff;box-shadow:0 0 4px #000;color:#fff;font:bold 13px/20px sans-serif;text-align:center">${i + 1}</div>`, 26) });
      m.bindTooltip(t + " (drag to search from here)");
      m.on("dragend", () => searchHere(m.getLatLng()));
      m.addTo(guessLayer);
    } else
      L.circleMarker([c.lat, c.lon], { radius: 9, color: "#fff", weight: 2, fillColor: col, fillOpacity: 1, bubblingMouseEvents: false })
        .on("click", () => applyGuess(i)).bindTooltip(t).addTo(guessLayer);
  });
}
export async function applyGuess(i, first) {
  const g = st.guesses, ph = st.photo; if (!g || !g.list[i] || !ph) return;
  const c = g.list[i]; g.pick = i;
  app.camPose = { ...c }; st.geoid = g.geoid;
  st.solve = { pose: c, ticks: [], predicted_x: [], residuals_px: [], auto: true, source: "guess" };
  $("camViewBtn").disabled = false; showCam(c); drawGuesses();
  if (first && !ces.on) map.setView([c.lat, c.lon], 15);
  setResult(guessText(c, i));
  renderRows(); draw(); dvRefresh(); await loadBuildings();
  if (st.photo === ph && st.guesses === g && g.pick === i)
    status(`Best guess: ${c.label || c.source} (score ${(+c.score).toFixed(2)}). Pick another on the map (Guesses).`);
}
export async function fetchGuesses(body, onPhoto) {
  const ph = st.photo, mine = ++app.guessSeq;
  const r = await postJson("/api/register/candidates", { photo: ph.path, ...body });
  if (mine !== app.guessSeq || st.photo !== ph) return null;          // photo / region changed, or a newer request started
  if (!r.ok || !(r.candidates || []).length) { status("No start guess: " + (r.error || r.reason || "none found") + "."); return null; }
  return r;
}
export async function loadGuesses() {                  // photo has no saved camera: preload the best guess in the background
  if (!st.photo) return;
  status("Finding start guesses…");
  try {
    const r = await fetchGuesses({ top_k: 5 });
    if (!r) return;
    st.guesses = { list: r.candidates, hints: r.hints || {}, geoid: r.geoid_m, pick: 0 };
    if (st.solve && st.solve.source !== "guess") { drawGuesses(); status("Start guesses ready (Guesses on the map)."); return; }  // user solved meanwhile
    await applyGuess(0, true);
  } catch (e) { status("Start guesses failed: " + e.message); }
}
export async function searchHere(ll) {
  const g = st.guesses; if (!g || !st.photo) return;
  status("Searching from here…");
  try {
    const r = await fetchGuesses({ viewpoints: [[ll.lat, ll.lng]], only_viewpoints: true, top_k: 3 });
    if (!r || st.guesses !== g) { drawGuesses(); return; }
    g.list = r.candidates.map(c => ({ ...c, source: "here", label: c.label || "Searched here" })).concat(g.list);
    g.geoid = r.geoid_m ?? g.geoid;
    await applyGuess(0);
  } catch (e) { status("Search failed: " + e.message); drawGuesses(); }
}
export function onGuessChk() { prefSet("photoLabelGuess", $("guessChk").checked ? "1" : "0"); drawGuesses(); }
