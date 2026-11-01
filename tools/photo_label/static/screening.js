// Background auto screening: the server flags street-level frames and panoramas; poll, then drop them from the list.
// Auto screening: the server flags street-level frames and stitched panoramas in the background;
// poll every 10 s, then drop what it flagged from the list (Undo unflags them all).
import { api, getJson, postJson } from "./api.js";
import { fillPhotoSel, flagPhoto, loadPhoto, showFlagged, syncFlagBtn } from "./photos.js";
import { app, st } from "./state.js";
import { $, status } from "./util.js";

export async function screenPhotos(seq) {
  try { await postJson("/api/photos/screen", {}); } catch (_) { return; }
  let r = null, fails = 0;
  for (;;) {
    await new Promise(ok => setTimeout(ok, 10000));
    if (seq !== app.regionSeq) return;
    try { r = await getJson(api("/api/photos/screen")); fails = 0; }
    catch (e) {
      if (++fails >= 5) { status("Auto screening stopped: the server stopped answering (" + (e.message || e) + ")."); return; }
      continue;
    }
    if (r.state !== "running") break;
  }
  const gone = st.photos.filter(p => r.flagged.includes(p.path) && !p.bad);
  if (!gone.length) return;
  if (showFlagged()) {                               // keep them listed, marked
    for (const p of gone) p.bad = true;
    fillPhotoSel(); syncFlagBtn();
    status(`Auto-flagged ${gone.length} photos (street level / panorama); they stay listed while "Show flagged" is on.`);
    return;
  }
  const was = st.photos.map(p => p.path), cur = st.photo && st.photo.path;
  st.photos = st.photos.filter(p => !r.flagged.includes(p.path));
  for (const p of gone) $("photoSel").querySelector(`option[value="${CSS.escape(p.path)}"]`)?.remove();
  if (cur && r.flagged.includes(cur)) {            // the open photo went: open the next one, as flagBtn does
    const next = st.photos.find(p => was.indexOf(p.path) > was.indexOf(cur)) || st.photos[st.photos.length - 1];
    if (next) { $("photoSel").value = next.path; await loadPhoto(next.path); } else status("No photos left.");
  }
  $("status").innerHTML = "";
  $("status").append(`Auto-flagged ${gone.length} photos (street level / panorama). `);
  const undo = document.createElement("a"); undo.href = "#"; undo.textContent = "Undo";
  undo.onclick = async e => {
    e.preventDefault();
    if (seq !== app.regionSeq) return;
    for (const p of gone) if (!(await flagPhoto(p.path, false))) return;
    st.photos = st.photos.concat(gone).sort((a, b) => was.indexOf(a.path) - was.indexOf(b.path));
    fillPhotoSel();
    status(`Restored ${gone.length} photos.`);
  };
  $("status").append(undo);
}
