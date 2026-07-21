<template>
  <CollapsibleSection title="📐 Mesh Import (STL/OBJ)" :start-open="false"
                      header-title="Import an STL/OBJ mesh as a heightmap layer, register it against the current DEM, and optionally merge it in.">

    <div class="mesh-import-source-row">
      <label class="btn btn-secondary mesh-import-upload-btn">
        📤 Upload STL/OBJ
        <input type="file" accept=".stl,.obj" style="display:none;" @change="onFileChosen">
      </label>
      <button class="btn btn-secondary" @click="toggleLibrary" :aria-expanded="showLibrary">
        📚 {{ showLibrary ? 'Hide' : 'Browse' }} Library
      </button>
    </div>

    <div v-if="showLibrary" class="mesh-import-library">
      <div v-if="libraryError" class="mesh-import-hint mesh-import-error">{{ libraryError }}</div>
      <div v-else-if="!libraryCities.length" class="mesh-import-hint">Loading library…</div>
      <div v-for="city in libraryCities" :key="city.city" class="mesh-import-city">
        <div class="mesh-import-city-name">{{ city.city }}</div>
        <div v-for="f in city.files" :key="f.rel_path"
             class="mesh-import-file-row" :class="{ selected: f.rel_path === selectedRelPath }"
             @click="selectLibraryFile(f)">
          <span class="mesh-import-file-name">{{ f.filename }}</span>
          <span v-if="f.location" class="mesh-import-file-badge" title="Location already saved for this file">📍</span>
          <span class="mesh-import-file-size">{{ (f.size_bytes / 1e6).toFixed(1) }} MB</span>
        </div>
      </div>
    </div>

    <div v-if="currentFilename" class="mesh-import-current">
      <span class="mesh-import-current-label">Selected:</span> {{ currentFilename }}
    </div>

    <div class="param-group" style="margin:4px 0;">
      <label for="meshUpAxis" title="Which mesh axis represents vertical height">Up axis</label>
      <select id="meshUpAxis" v-model="upAxis" class="ctrl-select" aria-label="Mesh up axis">
        <option value="z">Z (default)</option>
        <option value="-z">-Z</option>
        <option value="x">X</option>
        <option value="-x">-X</option>
        <option value="y">Y</option>
        <option value="-y">-Y</option>
      </select>
    </div>

    <div class="param-group" style="margin:4px 0;">
      <label for="meshResolution" title="Heightmap pixel size in metres — controls the raster's resolution, same as DEM resolution">Resolution (m/px)</label>
      <input type="number" id="meshResolution" v-model.number="resolutionM" min="0.1" max="100" step="0.5" class="ctrl-input" aria-label="Mesh heightmap resolution in metres per pixel">
    </div>

    <div class="param-group" style="margin:4px 0;">
      <label for="meshInfill" title="Fill gaps where no ray hit the mesh surface">Gap fill</label>
      <select id="meshInfill" v-model="infill" class="ctrl-select" aria-label="Mesh heightmap infill method">
        <option value="none">None</option>
        <option value="idw">Inverse-distance weighted</option>
        <option value="nearest">Nearest neighbour</option>
      </select>
    </div>

    <div v-if="showLibrary && selectedRelPath" class="row-gap6" style="margin:6px 0;">
      <button class="btn btn-secondary" style="flex:1;font-size:11px;" @click="saveLibraryLocation">
        💾 Save location for this city
      </button>
    </div>

    <div class="row-gap6" style="margin:6px 0;">
      <button id="meshAutoRegisterBtn" class="btn btn-secondary mesh-import-action-btn" style="flex:1;"
              :disabled="!hasSource || autoRunning" @click="autoRegister"
              title="Geocode the filename and try automatic registration against OpenStreetMap — always opens the manual picker afterward for you to confirm or adjust">
        {{ autoRunning ? '⏳ Running…' : '🤖 Auto (geocode + register)' }}
      </button>
    </div>
    <div v-if="autoStatusLabel" class="mesh-import-hint" :class="{ 'mesh-import-error': autoStatusIsError }">{{ autoStatusLabel }}</div>

    <div class="row-gap6 mesh-import-actions">
      <button id="meshComputeHeightmapBtn" class="btn btn-secondary mesh-import-action-btn"
              :disabled="!hasSource" @click="computeHeightmap"
              title="Convert the mesh to a heightmap for the current DEM bbox">🗺 Preview Heightmap</button>
      <button id="meshRegisterBtn" class="btn btn-secondary mesh-import-action-btn"
              :disabled="!hasHeightmap" @click="openRegistration"
              title="Manually register the heightmap against the current DEM">🎯 Register…</button>
      <button id="meshApplyToDemBtn" class="btn btn-primary mesh-import-action-btn"
              :disabled="!hasRegistered" @click="applyToDem"
              title="Replace DEM heights with the registered mesh in its footprint">✓ Apply to DEM</button>
    </div>
    <span id="meshImportStats" class="mesh-import-stats">{{ statsLabel }}</span>
    <div class="mesh-import-footer-hint">Auto mode geocodes the filename, runs automatic OSM registration, and matches/creates a region — then always opens the manual picker to confirm. Manual registration requires ≥3 matched point pairs.</div>
  </CollapsibleSection>
</template>
<script setup lang="ts">
import CollapsibleSection from '../shared/CollapsibleSection.vue';
import { onBeforeUnmount, onMounted, ref } from 'vue';

/** Suggest a resolution (m/px) for the current DEM bbox — delegates to
 * mesh-layer.js's window.suggestedMeshResolutionM so both the UI's initial
 * slider value and the actual heightmap-fetch default (including auto mode,
 * which doesn't go through this component) use the exact same calculation. */
function _suggestedResolution(): number {
  return (window as any).suggestedMeshResolutionM?.((window as any).appState?.currentDemBbox) ?? 5.0;
}

const showLibrary = ref(false);
const libraryCities = ref<any[]>([]);
const libraryError = ref('');
const selectedRelPath = ref('');
const currentFilename = ref('');
const upAxis = ref('z');
// A flat 5m default errors immediately for a typical region-sized bbox (the
// server rejects grids over MAX_DIM=2000px/side to avoid ray-casting a
// 100M+ pixel grid) — seed from the current DEM bbox instead, targeting a
// grid comfortably under that cap. Falls back to 5 if no bbox is loaded yet.
const resolutionM = ref(_suggestedResolution());
const infill = ref('none');
const hasHeightmap = ref(false);
const hasRegistered = ref(false);
const statsLabel = ref('');
// window.appState.meshImport is a plain object mutated outside Vue's
// reactivity (set by mesh-layer.js), so a computed() reading it never
// re-evaluates. Track source presence as a local ref instead, set
// directly by the handlers below.
const hasSource = ref(false);
const autoRunning = ref(false);
const autoStatusLabel = ref('');
const autoStatusIsError = ref(false);

async function autoRegister() {
  autoRunning.value = true;
  autoStatusLabel.value = '';
  autoStatusIsError.value = false;
  try {
    const result = await (window as any).autoRegisterMesh({ resolution: 512 });
    if (!result) {
      autoStatusIsError.value = true;
      autoStatusLabel.value = 'Auto mode failed — see toast for details.';
      return;
    }
    if (result.status !== 'ok') {
      autoStatusIsError.value = true;
      autoStatusLabel.value = result.status === 'unavailable'
        ? 'Auto mode is unavailable on this server.'
        : `Couldn't geocode "${result.city_name || '?'}" — try the manual picker.`;
      return;
    }
    const region = result.region || {};
    autoStatusLabel.value =
      `${result.city_name}: ${region.created ? 'created new region' : 'matched region'} ` +
      `"${region.name}". confidence=${result.confidence?.toFixed(2)} ` +
      `footprint IoU=${result.footprint_iou?.toFixed(2)} rmse=${result.rmse_m?.toFixed(1)}m — ` +
      `review the alignment in the picker below.`;
    // The heightmap/register buttons' enabled state reflects appState set by
    // mesh-layer.js's computeMeshHeightmap (called internally by autoRegisterMesh).
    hasHeightmap.value = !!(window as any).appState?.meshImport?.heightmap;
    resolutionM.value = _suggestedResolution();
  } finally {
    autoRunning.value = false;
  }
}

async function toggleLibrary() {
  showLibrary.value = !showLibrary.value;
  if (showLibrary.value && !libraryCities.value.length) await loadLibrary();
}

async function loadLibrary() {
  const { data, error } = await (window as any).api.mesh.library();
  if (error) { libraryError.value = error; return; }
  libraryCities.value = data.cities;
}

function onFileChosen(e: Event) {
  const input = e.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  selectedRelPath.value = '';
  currentFilename.value = file.name;
  hasHeightmap.value = false;
  hasRegistered.value = false;
  hasSource.value = false;
  resolutionM.value = _suggestedResolution();
  (window as any).uploadMeshLayer(file).then(() => {
    const uploaded = !!(window as any).appState?.meshImport?.uploadId;
    hasSource.value = uploaded;
    if (uploaded) currentFilename.value = (window as any).appState?.meshImport?.filename || file.name;
  });
  input.value = '';
}

function selectLibraryFile(f: any) {
  selectedRelPath.value = f.rel_path;
  currentFilename.value = f.filename;
  hasHeightmap.value = false;
  hasRegistered.value = false;
  resolutionM.value = _suggestedResolution();
  (window as any).selectLibraryMeshFile(f.rel_path, f.filename);
  hasSource.value = true;
  if (f.location) {
    upAxis.value = f.location.up_axis || 'z';
  }
}

async function computeHeightmap() {
  await (window as any).computeMeshHeightmap({
    resolutionM: resolutionM.value, upAxis: upAxis.value, infill: infill.value,
  });
  const hm = (window as any).appState?.meshImport?.heightmap;
  hasHeightmap.value = !!hm;
  if (hm) {
    statsLabel.value = `${hm.width}×${hm.height}px, ${hm.validPct.toFixed(0)}% valid`;
  }
}

function openRegistration() {
  (window as any).openMeshRegistrationModal();
}

function _onMeshRegistered(e: Event) {
  const reg = (e as CustomEvent).detail;
  hasRegistered.value = !!reg;
  if (reg) statsLabel.value += ` — RMS ${reg.rmsResidualPx.toFixed(1)}px`;
}

function applyToDem() {
  (window as any).applyMeshToDem();
}

async function saveLibraryLocation() {
  const bbox = (window as any).appState?.currentDemBbox;
  if (!bbox || !selectedRelPath.value) {
    (window as any).showToast?.('Select a region first.', 'warning');
    return;
  }
  const { data, error } = await (window as any).api.mesh.setLibraryLocation(selectedRelPath.value, {
    north: bbox.north, south: bbox.south, east: bbox.east, west: bbox.west,
    up_axis: upAxis.value, notes: '', apply_to_city: true,
  });
  if (error) {
    (window as any).showToast?.('Save location failed: ' + error, 'error');
    return;
  }
  (window as any).showToast?.(`Location saved for ${data.updated.length} file(s)`, 'success');
  await loadLibrary();
}

onMounted(() => {
  if (showLibrary.value) loadLibrary();
  window.addEventListener('mesh-import-registered', _onMeshRegistered);
});

onBeforeUnmount(() => {
  window.removeEventListener('mesh-import-registered', _onMeshRegistered);
});
</script>
<style scoped>
.mesh-import-source-row {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}
.mesh-import-upload-btn {
  cursor: pointer;
}
.mesh-import-library {
  max-height: 220px;
  overflow-y: auto;
  border: 1px solid #333;
  border-radius: 4px;
  padding: 6px;
  margin-bottom: 8px;
  background: #1e1e1e;
}
.mesh-import-city-name {
  font-size: 10px;
  color: #8ea6bf;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin: 6px 0 2px;
}
.mesh-import-file-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 4px;
  font-size: 11px;
  color: #ccc;
  cursor: pointer;
  border-radius: 3px;
}
.mesh-import-file-row:hover {
  background: #2a2a2a;
}
.mesh-import-file-row.selected {
  background: #2d4a63;
  color: #fff;
}
.mesh-import-file-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mesh-import-file-size {
  font-size: 9px;
  color: #888;
}
.mesh-import-current {
  font-size: 11px;
  color: #aaa;
  margin-bottom: 6px;
}
.mesh-import-current-label {
  color: #8ea6bf;
}
.mesh-import-hint {
  font-size: 11px;
  color: #888;
  padding: 4px;
}
.mesh-import-error {
  color: #e08080;
}
.mesh-import-actions {
  margin: 6px 0;
  flex-wrap: wrap;
}
.mesh-import-action-btn {
  flex: 1;
  min-width: 120px;
  font-size: 11px;
  padding: 5px 6px;
}
.mesh-import-stats {
  font-size: 10px;
  color: #888;
  display: block;
  margin-top: 4px;
}
.mesh-import-footer-hint {
  font-size: 10px;
  color: #666;
  margin-top: 4px;
}
</style>
