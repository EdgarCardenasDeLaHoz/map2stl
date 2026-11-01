// Every fetch to the tool's server. The region goes on every call (the Google tile proxy does not need it).
import { app } from "./state.js";
import { $ } from "./util.js";

export const api = p => p + (p.includes("?") ? "&" : "?") + "region=" + encodeURIComponent(app.curRegion);
// GET + json that checks the status: a failure throws an Error saying what failed (the server's own
// message when it sent one), so callers can show it on the status line.
export async function getJson(url) {
  const what = url.split("?")[0];
  let r;
  try { r = await fetch(url); } catch (e) { throw new Error(`${what}: ${e.message || e}`); }
  if (!r.ok) {
    let why = "";
    try { const j = await r.json(); why = j.error || j.reason || ""; } catch (_) { /* not json */ }
    throw new Error(`${what}: HTTP ${r.status}${why ? " - " + why : ""}`);
  }
  return r.json();
}
// POST: the callers read the answer's own ok / error / reason fields (a refused solve is a normal answer), so it
// returns the body whatever the status; only a network failure or a non-json answer throws.
export const postJson = (url, body) =>
  fetch(url, { method: "POST", body: JSON.stringify({ ...body, region: app.curRegion }) }).then(r => r.json());
