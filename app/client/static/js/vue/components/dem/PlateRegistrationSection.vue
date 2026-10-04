<template>
  <CollapsibleSection title="🧭 Plate registration" sub="plate" :start-open="false"
                      header-title="Place a library plate on the map by its streets, check the placement by tile consensus, and save it to the plate's location sidecar.">

    <div class="param-group plate-reg-row">
      <label for="plateRegFile" title="A mesh-library file; the saved location applies to its whole city folder">Plate</label>
      <select id="plateRegFile" v-model="relPath" class="ctrl-select" aria-label="Library plate" @change="onPlateChosen">
        <option value="">— choose a library plate —</option>
        <optgroup v-for="city in libraryCities" :key="city.city" :label="city.city">
          <option v-for="f in city.files" :key="f.rel_path" :value="f.rel_path">
            {{ f.filename }}{{ f.location ? ' 📍' : '' }}
          </option>
        </optgroup>
      </select>
    </div>
    <div v-if="libraryError" class="plate-reg-hint plate-reg-error">{{ libraryError }}</div>

    <div class="param-group plate-reg-row">
      <label for="plateRegPack" title="The align-tool pack (rasters exported by tools/align_tool) the placement runs on">Pack</label>
      <select id="plateRegPack" v-model="slug" class="ctrl-select" aria-label="Align pack">
        <option value="">— no pack —</option>
        <option v-for="p in packs" :key="p.slug" :value="p.slug" :disabled="!p.scorable">
          {{ p.city }} ({{ p.slug }}){{ p.verdict ? ' · ' + p.verdict : '' }}
        </option>
      </select>
    </div>
    <div v-if="relPath && !slug && packsLoaded" class="plate-reg-hint">
      No pack matches this plate. Export one with <code>tools/align_tool/export_align_data.py</code>, or pick one above.
    </div>

    <div class="plate-reg-options">
      <label title="Search ±400 m / ±6° / 0.85–1.20× around the pack's pose by footprints, water with bridges and terrain. Minutes when OSM is not cached.">
        <input type="checkbox" v-model="place"> Street placement
      </label>
      <label title="If the tiles agree on a different position, move the placement there (bounded, only when it raises the lead)">
        <input type="checkbox" v-model="fix"> Tile correction
      </label>
    </div>

    <div class="row-gap6" style="margin:6px 0;">
      <button class="btn btn-primary" style="flex:1;font-size:11px;" :disabled="!slug || running" @click="run">
        {{ running ? '⏳ Running…' : (place ? '▶ Place + verify' : '▶ Verify current placement') }}
      </button>
      <button v-if="running" class="btn btn-secondary" style="font-size:11px;" @click="cancel">✕ Cancel</button>
    </div>

    <div v-if="task" class="plate-reg-progress" role="status" aria-live="polite">
      <div class="plate-reg-bar"><i :style="{ width: (task.progress || 0) + '%' }"></i></div>
      <span>{{ task.message }} <span class="plate-reg-muted">({{ task.elapsed_s }} s)</span></span>
    </div>
    <div v-if="errorMsg" class="plate-reg-hint plate-reg-error">{{ errorMsg }}</div>

    <div v-if="result" class="plate-reg-result">
      <div class="plate-reg-verdict" :class="'v-' + (result.verdict?.status || 'none')">
        {{ (result.verdict?.status || '?').toUpperCase() }}
        <span class="plate-reg-muted">{{ result.city }} · {{ result.matrix_source }}</span>
      </div>
      <ul v-if="result.verdict?.reasons?.length" class="plate-reg-reasons">
        <li v-for="(r, i) in result.verdict.reasons" :key="i">{{ r }}</li>
      </ul>
      <table aria-label="Plate registration result">
        <tbody>
          <tr v-for="row in resultRows" :key="row.label" :title="row.hint">
            <th>{{ row.label }}</th><td>{{ row.value }}</td>
          </tr>
        </tbody>
      </table>
      <div class="row-gap6" style="margin-top:6px;">
        <button class="btn btn-secondary" style="flex:1;font-size:11px;" @click="showOnMap">🗺 Show on map</button>
        <button class="btn btn-primary" style="flex:1;font-size:11px;" :disabled="!relPath || saving" @click="savePlacement"
                title="Write the placement's bbox (and the full placement record) to the plate's .location.json sidecar, for every file in its city folder">
          {{ saving ? '⏳ Saving…' : '💾 Save to sidecar' }}
        </button>
      </div>
      <a href="/reports" target="_blank" rel="noopener" class="plate-reg-link">📄 Registration reports and packs</a>
    </div>
  </CollapsibleSection>
</template>

<script setup lang="ts">
/**
 * PlateRegistrationSection — F-REGION §5 "registration in the UI".
 *
 * Runs the align tool's street placement + tile-consensus verdict on the server as a
 * background task (/api/registration/plate/*), polls it, draws the placed plate on the
 * Leaflet map, and saves the placement through the existing library location route
 * (POST /api/layers/mesh/library/{rel_path}/location, apply_to_city).
 */
import CollapsibleSection from '../shared/CollapsibleSection.vue';
import { computed, onBeforeUnmount, ref } from 'vue';

type Pack = { slug: string; city: string; scorable: boolean; placeable: boolean; verdict: string | null };

const api = () => (window as any).api;
const toast = (msg: string, kind = 'info') => (window as any).showToast?.(msg, kind);

const libraryCities = ref<any[]>([]);
const libraryError = ref('');
const packs = ref<Pack[]>([]);
const packsLoaded = ref(false);
const relPath = ref('');
const slug = ref('');
const place = ref(true);
const fix = ref(true);
const running = ref(false);
const saving = ref(false);
const task = ref<any>(null);
const result = ref<any>(null);
const errorMsg = ref('');
let pollTimer: ReturnType<typeof setTimeout> | null = null;
let mapLayer: any = null;

const fmt = (v: any, digits = 1, unit = '') =>
  (typeof v === 'number' && Number.isFinite(v)) ? `${v.toFixed(digits)}${unit}` : '—';

const resultRows = computed(() => {
  const r = result.value;
  if (!r) return [];
  const v = r.verdict || {};
  const p = r.placement || {};
  const g = r.geometry || {};
  const rows = [
    { label: 'Lead', value: fmt(v.lead, 2), hint: "Share of the tiles' weight on this position vs the best rival (pass ≥ 0.65, fail < 0.45)" },
    { label: 'Tiles', value: `${v.endorsing ?? '—'}/${v.voting ?? '—'}`, hint: 'Tiles endorsing the placement / tiles that could vote' },
    { label: 'Tile correction', value: fmt(v.corrected_m, 0, ' m'), hint: 'How far the tiles moved the placement' },
    { label: 'Centre', value: g.center ? `${g.center.lat.toFixed(5)}, ${g.center.lon.toFixed(5)}` : '—', hint: 'Placed plate centre (lat, lon)' },
    { label: 'Turn', value: fmt(g.turn_deg, 1, '°'), hint: 'Plate rotation on the map' },
    { label: 'Size', value: g.width_m ? `${g.width_m.toFixed(0)} × ${g.height_m.toFixed(0)} m` : '—', hint: 'Ground the plate covers' },
  ];
  if (r.placement) {
    rows.push(
      { label: 'Moved', value: fmt(p.moved_m, 0, ' m'), hint: "Street placement's move from the pack's recorded pose" },
      { label: 'Unique / size margin', value: `${fmt(p.unique, 2)} / ${fmt(p.size_margin, 2)}`, hint: 'Lead over any place > 80 m away / any size > 6% different; confident when both ≥ 1' },
      { label: 'Confident', value: p.confident ? 'yes' : (p.position_confident ? 'position only' : 'no'), hint: 'A position-only placement keeps the pack size' },
      { label: 'Channels', value: (p.channels || []).join(', ') || '—', hint: 'Map layers the placement compared' },
    );
  }
  return rows;
});

/** window.api comes from a plain script that runs after the Vue bundle mounts. */
async function apiReady(timeoutMs = 10000) {
  const t0 = Date.now();
  while (!(window as any).api && Date.now() - t0 < timeoutMs) {
    await new Promise((r) => setTimeout(r, 100));
  }
  return !!(window as any).api;
}

async function loadLibrary() {
  if (!(await apiReady())) { libraryError.value = 'API not available'; return; }
  const { data, error } = await api().mesh.library();
  if (error) { libraryError.value = error; return; }
  libraryCities.value = data.cities || [];
}

async function loadPacks() {
  if (!(await apiReady())) return;
  const { data, error } = await api().registration.packs();
  packsLoaded.value = true;
  if (error) { errorMsg.value = 'Could not list align packs: ' + error; return; }
  packs.value = data.packs || [];
}

async function onPlateChosen() {
  result.value = null;
  if (!relPath.value) return;
  const { data } = await api().registration.match(relPath.value);
  slug.value = data?.slug || '';
}

function stopPolling() {
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = null;
}

async function poll(taskId: string) {
  const { data, error } = await api().registration.status(taskId);
  if (error) {
    running.value = false;
    errorMsg.value = error;
    return;
  }
  task.value = data;
  if (data.status === 'running') {
    pollTimer = setTimeout(() => poll(taskId), 1500);
    return;
  }
  running.value = false;
  if (data.status === 'done') {
    result.value = data.result;
    showOnMap();
    toast(`Plate registration: ${data.result?.verdict?.status}`, data.result?.verdict?.status === 'pass' ? 'success' : 'warning');
  } else if (data.status === 'error') {
    errorMsg.value = data.error || data.message;
  }
}

async function run() {
  stopPolling();
  errorMsg.value = '';
  result.value = null;
  running.value = true;
  const { data, error } = await api().registration.start({
    slug: slug.value, place: place.value, fix: fix.value, rel_path: relPath.value || null,
  });
  if (error) {
    running.value = false;
    errorMsg.value = error;
    return;
  }
  task.value = data;
  poll(data.task_id);
}

async function cancel() {
  if (!task.value) return;
  stopPolling();
  await api().registration.cancel(task.value.task_id);
  running.value = false;
  task.value = { ...task.value, message: 'Cancelled' };
}

function clearMapLayer() {
  if (mapLayer) {
    try { mapLayer.remove(); } catch { /* map gone */ }
    mapLayer = null;
  }
}

function showOnMap() {
  const L = (window as any).L;
  const map = (window as any).getMap?.();
  const g = result.value?.geometry;
  if (!L || !map || !g?.corners) return;
  clearMapLayer();
  const status = result.value?.verdict?.status;
  const color = status === 'pass' ? '#2ea043' : status === 'review' ? '#e6b428' : '#d62828';
  const outline = L.polygon(g.corners, { color, weight: 2, fillOpacity: 0.08 });
  outline.bindTooltip(`${result.value.city}: ${status}`, { sticky: true });
  const w = result.value.window;
  const windowRect = w ? L.rectangle([[w.south, w.west], [w.north, w.east]],
    { color: '#888', weight: 1, dashArray: '4 4', fill: false }) : null;
  mapLayer = L.layerGroup(windowRect ? [windowRect, outline] : [outline]).addTo(map);
  try { map.fitBounds(outline.getBounds(), { padding: [30, 30] }); } catch { /* hidden map */ }
}

async function savePlacement() {
  const r = result.value;
  if (!r || !relPath.value) return;
  saving.value = true;
  const g = r.geometry;
  const placement = {
    pack: r.slug, source: r.matrix_source, center: g.center, turn_deg: g.turn_deg,
    width_m: g.width_m, height_m: g.height_m, corners: g.corners,
    verdict: r.verdict?.status, lead: r.verdict?.lead,
    confident: r.placement ? r.placement.confident : null,
    saved: new Date().toISOString(),
  };
  const { data, error } = await api().mesh.setLibraryLocation(relPath.value, {
    ...g.bbox, up_axis: 'z', apply_to_city: true, placement,
    notes: `plate registration: ${r.verdict?.status} (${r.slug}, ${r.matrix_source})`,
  });
  saving.value = false;
  if (error) { toast('Save failed: ' + error, 'error'); return; }
  toast(`Placement saved to ${data.updated.length} sidecar(s)`, 'success');
  loadLibrary();
}

// Lazy: the section starts collapsed, but listing is cheap (one directory scan each).
loadLibrary();
loadPacks();

onBeforeUnmount(() => {
  stopPolling();
  clearMapLayer();
});
</script>

<style scoped>
.plate-reg-row { margin: 4px 0; }
.plate-reg-row select { max-width: 100%; }
.plate-reg-options {
  display: flex;
  gap: 12px;
  font-size: 11px;
  color: #ccc;
  margin: 4px 0;
}
.plate-reg-hint { font-size: 11px; color: var(--text-dim); padding: 4px; }
.plate-reg-error { color: #e08080; }
.plate-reg-muted { color: var(--text-dim); font-size: 11px; }
.plate-reg-progress { font-size: 11px; color: #ccc; margin: 4px 0; }
.plate-reg-bar {
  height: 4px;
  background: #333;
  border-radius: 2px;
  overflow: hidden;
  margin-bottom: 3px;
}
.plate-reg-bar i { display: block; height: 100%; background: #4a90d9; transition: width .3s; }
.plate-reg-result { font-size: 11px; margin-top: 6px; }
.plate-reg-verdict { font-weight: 700; font-size: 12px; margin-bottom: 4px; }
.plate-reg-verdict.v-pass { color: #4cc26a; }
.plate-reg-verdict.v-review { color: #e6b428; }
.plate-reg-verdict.v-fail { color: #e06060; }
.plate-reg-reasons { margin: 2px 0 6px 16px; padding: 0; color: #bbb; }
.plate-reg-result table { width: 100%; border-collapse: collapse; }
.plate-reg-result th { text-align: left; font-weight: normal; color: #8ea6bf; padding: 1px 4px; }
.plate-reg-result td { text-align: right; color: #ddd; padding: 1px 4px; font-variant-numeric: tabular-nums; }
.plate-reg-link { display: inline-block; margin-top: 6px; color: #7fb3ff; }
</style>
