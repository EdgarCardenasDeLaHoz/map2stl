// Header and readout bits that several modules write: the "Saved" state, the camera readout chip, the context pill.
// Imports only state and util so any module can use it.
import { app, st } from "./state.js";
import { $, esc } from "./util.js";

// kind: ok | busy | err
export function setSaved(kind, msg) {
  const e = $("saved");
  e.className = "saved " + kind;
  e.textContent = kind === "ok" ? "✓ Saved" : kind === "busy" ? "Saving…" : "⚠ Not saved";
  e.title = msg || (kind === "ok" ? "Every change to the pairs is saved automatically" : "");
}
const f = (v, u, d = 0) => v == null || Number.isNaN(+v) ? "–" : (+v).toFixed(d) + u;
// The camera readout: heading, pitch, elevation and a score, from whatever camera is current.
export function setPose() {
  const el = $("pose"), s = st.solve, q = s && s.pose;
  if (!q) { el.hidden = true; el.textContent = ""; return; }
  let t = `heading ${f(q.heading_deg, "°")}, pitch ${f(q.pitch_deg ?? (s.source === "guess" ? 0 : null), "°")}, elev ${f(q.elev_m, " m")}`;
  if (s.source === "guess") t = `Camera guess${q.label || q.source ? " (" + (q.label || q.source) + ")" : ""}: ${t}, score ${f(q.score, "", 2)}`;
  else if (s.source === "auto") t = `Auto register: ${t}, ${st.auto.length} auto pairs`;
  else t = `Solved: ${t}${q.rms_px == null ? "" : ", rms " + f(q.rms_px, " px", 1)}`;
  el.textContent = t; el.title = t; el.hidden = false;
}
// The long result line (Solve details) plus the short readout.
export function setResult(text) { $("result").textContent = text; setPose(); }
// The context pill and the card captions: region · photo, photo size, base map.
export function syncCtx() {
  const o = $("region").selectedOptions[0], label = o ? (o.dataset.label || o.textContent) : (app.curRegion || "");
  // Many photos are <folder>/photo.png, so a bare "photo" stem names the folder instead.
  const parts = st.photo ? st.photo.path.split("/") : [], stem = (parts.pop() || "").replace(/\.[^.]+$/, "");
  const name = !st.photo ? "no photo" : (stem === "photo" && parts.length ? parts.pop() : stem);
  $("ctxBtn").innerHTML = `<b>${esc(label)}</b><span class="d">·</span><span class="ctxPhoto">${esc(name)}</span><span class="d">▾</span>`;
  $("photoDims").textContent = st.photo ? `${st.photo.w} × ${st.photo.h}` : "";
  $("mapNote").textContent = `${label} · ${app.satOn ? "satellite" : "street"}`;
}
