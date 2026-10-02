<template>
  <CollapsibleSection id="cityLandmarksSection" title="🏛 Landmarks" :start-open="false"
                      header-title="Notable buildings of the loaded city data: build them from OSM parts, a surveyed nDSM, or an uploaded mesh (F-LANDMARK)">
    <div class="lm-head">
      <button id="findLandmarksBtn" class="btn btn-secondary btn-sm" :disabled="!!busy" @click="loadList"
              title="List places of worship, town halls, castles, attractions and the tallest buildings of the loaded city buildings">
        {{ busy === 'list' ? 'Finding…' : (items.length ? '↻ Refresh' : '🔎 Find landmarks') }}
      </button>
      <label class="lm-muted" title="Also list this many of the tallest other buildings">
        tallest <input v-model.number="tallestN" type="number" min="0" max="50" class="ctrl-input-sm lm-num" aria-label="Tallest N">
      </label>
      <span class="lm-muted">{{ regionName ? regionName : 'no region: overrides are not saved' }}</span>
    </div>

    <div v-if="!hasBuildings" class="lm-muted">Load the city layer (Fetch → City) first.</div>
    <div v-if="idsMissing" class="lm-warn">
      This city data was cached before OSM ids were kept, so no building can be overridden.
      Reload the city layer to re-fetch the buildings.
    </div>
    <div v-if="errorMsg" class="lm-error">{{ errorMsg }}</div>

    <table v-if="items.length" id="landmarksTable" class="lm-table" aria-label="Landmarks">
      <thead>
        <tr><th>Building</th><th>H</th><th>Parts</th><th>Roofs</th><th>Source</th></tr>
      </thead>
      <tbody>
        <tr v-for="it in items" :key="it.osm_id || it.index" :class="{ selected: selected?.index === it.index }"
            :data-osm-id="it.osm_id || ''" @click="select(it)">
          <td>
            <div class="lm-name">{{ it.name || it.building || `Building ${it.index + 1}` }}</div>
            <div class="lm-sub">{{ CATEGORY_LABELS[it.category] || it.category }} · {{ it.osm_id || 'no id' }}</div>
          </td>
          <td class="lm-numcell">{{ it.height_m != null ? it.height_m.toFixed(0) + ' m' : '—' }}
            <div class="lm-sub">{{ it.height_source || '' }}</div></td>
          <td class="lm-numcell">{{ it.parts }}</td>
          <td class="lm-sub">{{ it.roof_shapes.join(', ') }}</td>
          <td :class="{ 'lm-overridden': isOverridden(it) }">{{ overrideLabel(overrides[it.osm_id]) }}</td>
        </tr>
      </tbody>
    </table>

    <div v-if="selected" id="landmarkEditor" class="lm-editor">
      <div class="lm-editor-title">{{ selected.name || selected.building || `Building ${selected.index + 1}` }}
        <span class="lm-sub">{{ selected.parts }} part{{ selected.parts === 1 ? '' : 's' }} · {{ selected.area_m2.toFixed(0) }} m²</span></div>

      <div class="lm-kinds" role="radiogroup" aria-label="Landmark source">
        <label><input v-model="draft.kind" type="radio" value="osm" name="lmKind"> OSM parts</label>
        <label :title="ndsmAvailable ? '' : 'No surveyed nDSM covers this building'">
          <input v-model="draft.kind" type="radio" value="ndsm" name="lmKind" :disabled="!ndsmAvailable"> nDSM</label>
        <label><input v-model="draft.kind" type="radio" value="mesh" name="lmKind"> Uploaded mesh</label>
      </div>

      <template v-if="draft.kind === 'ndsm'">
        <div class="param-group lm-row">
          <label for="lmProvider">Provider</label>
          <select id="lmProvider" v-model="draft.provider" class="ctrl-input-sm">
            <option value="auto">Auto (best covering)</option>
            <option v-for="s in sources" :key="s.name" :value="s.name" :disabled="!s.available">
              {{ s.label }}{{ s.available ? '' : (s.note ? ' — ' + s.note : ' — no coverage') }}
            </option>
          </select>
        </div>
        <div class="param-group lm-row">
          <label for="lmRes">Resolution (m)</label>
          <input id="lmRes" v-model.number="draft.resolution_m" type="number" min="0.25" max="10" step="0.25"
                 class="ctrl-input-sm lm-num" placeholder="native">
        </div>
      </template>

      <template v-if="draft.kind === 'mesh'">
        <div class="lm-row">
          <label class="btn btn-secondary btn-sm">📤 {{ draft.filename || 'Upload STL / OBJ / glTF' }}
            <input type="file" accept=".stl,.obj,.glb,.gltf" style="display:none" aria-label="Landmark mesh file" @change="onFile">
          </label>
        </div>
        <div class="lm-grid">
          <label>Fit <select v-model="draft.fit" class="ctrl-input-sm"><option value="rectangle">Footprint rectangle</option><option value="uniform">Uniform</option></select></label>
          <label>Up <select v-model="draft.up_axis" class="ctrl-input-sm"><option value="auto">Auto</option><option value="z">Z</option><option value="y">Y (glTF)</option></select></label>
          <label>Rotate ° <input v-model.number="draft.rotation_deg" type="number" step="5" class="ctrl-input-sm lm-num"></label>
          <label>Scale <input v-model.number="draft.scale" type="number" step="0.05" min="0.1" class="ctrl-input-sm lm-num"></label>
          <label>Offset E m <input v-model.number="draft.offset_x_m" type="number" step="0.5" class="ctrl-input-sm lm-num"></label>
          <label>Offset N m <input v-model.number="draft.offset_y_m" type="number" step="0.5" class="ctrl-input-sm lm-num"></label>
          <label>Height <select v-model="draft.vertical" class="ctrl-input-sm"><option value="true">Mesh proportions</option><option value="fit">Fit to height</option></select></label>
          <label v-if="draft.vertical === 'fit'">m <input v-model.number="draft.height_m" type="number" min="1" step="1" class="ctrl-input-sm lm-num" :placeholder="String(selected.height_m ?? '')"></label>
        </div>
      </template>

      <div class="lm-actions">
        <button id="landmarkPreviewBtn" class="btn btn-secondary btn-sm" :disabled="!!busy || !selected.osm_id || !draftReady" @click="preview">
          {{ busy === 'preview' ? 'Building…' : '👁 Preview' }}</button>
        <button id="landmarkSaveBtn" class="btn btn-primary btn-sm" :disabled="!!busy || !selected.osm_id || !draftReady" @click="save"
                :title="regionName ? 'Save for this region; sent with the City Model build' : 'Kept for this session (no region selected)'">💾 Save</button>
        <button v-if="isOverridden(selected)" class="btn btn-secondary btn-sm" :disabled="!!busy" @click="reset">Reset to OSM</button>
      </div>
      <div v-if="previewInfo" class="lm-muted" id="landmarkPreviewInfo">{{ previewInfo }}</div>
      <div v-show="hasPreview" ref="viewEl" class="lm-view" title="Drag to rotate, wheel to zoom"></div>
    </div>
  </CollapsibleSection>
</template>

<script setup lang="ts">
/**
 * CityLandmarksSection — the Landmarks panel (F-LANDMARK §5).
 *
 * Lists notable buildings of appState.osmCityData (POST /api/cities/landmarks:
 * places of worship, town halls, castles, attractions + the tallest N), with part
 * count, roof shapes, height and height source. Per landmark the source can be
 * OSM parts (default), a surveyed nDSM (provider list from the same call) or an
 * uploaded mesh (POST /api/layers/mesh/upload); "Preview" builds that landmark
 * alone (POST /api/cities/landmarks/preview) into a small three.js view, "Save"
 * stores the override for the region (PUT /api/regions/{name}/landmarks/{osm_id})
 * and appState.cityLandmarkOverrides, which the City Model build sends as
 * landmark_overrides (export-handlers.js).
 */
import { computed, nextTick, onBeforeUnmount, ref, toRaw, watch } from 'vue';
import CollapsibleSection from '../shared/CollapsibleSection.vue';
import { useAppStore } from '../../stores/app';
import { buildingsWithOverrides } from '../../../modules/layers/building-heights.js';
import {
  CATEGORY_LABELS, draftFromSpec, meshBounds, overrideLabel, specFromDraft,
} from '../../../modules/layers/landmark-overrides.js';

const store = useAppStore();
const api = () => (window as any).api;

const items = ref<any[]>([]);
const sources = ref<any[]>([]);
const selected = ref<any>(null);
const draft = ref(draftFromSpec(undefined));
const busy = ref<'' | 'list' | 'preview' | 'save' | 'upload'>('');
const errorMsg = ref('');
const idsMissing = ref(false);
const tallestN = ref(10);
const previewInfo = ref('');
const hasPreview = ref(false);
const viewEl = ref<HTMLElement | null>(null);

const regionName = computed<string>(() => (store as any).selectedRegion?.name || '');
const features = computed<any[]>(() => (store as any).osmCityData?.buildings?.features || []);
const hasBuildings = computed(() => features.value.length > 0);
const overrides = computed<Record<string, any>>(() => (store as any).cityLandmarkOverrides || {});
const ndsmAvailable = computed(() => sources.value.some((s) => s.available));
const draftReady = computed(() => draft.value.kind !== 'mesh' || !!draft.value.upload_id);

function isOverridden(it: any) {
  const k = it?.osm_id && overrides.value[it.osm_id]?.kind;
  return !!k && k !== 'osm';
}

/** The loaded buildings as a clean FeatureCollection (no client-only fields). */
function buildingsPayload(bbox?: any) {
  // Raw: serialising thousands of reactive proxies is what makes a big city slow.
  let feats = toRaw(features.value);
  if (bbox) {
    // Only the features around one landmark (its outline and parts).
    const pad = 0.0002;
    feats = feats.filter((f: any) => {
      const b = _bounds(f?.geometry);
      return b && b[0] <= bbox.east + pad && b[2] >= bbox.west - pad
        && b[1] <= bbox.north + pad && b[3] >= bbox.south - pad;
    });
  }
  return buildingsWithOverrides(feats, {});
}

function _bounds(geom: any): [number, number, number, number] | null {
  // [minLon, minLat, maxLon, maxLat]
  let x0 = Infinity; let y0 = Infinity; let x1 = -Infinity; let y1 = -Infinity;
  const walk = (n: any) => {
    if (!Array.isArray(n)) return;
    if (typeof n[0] === 'number') {
      x0 = Math.min(x0, n[0]); x1 = Math.max(x1, n[0]); y0 = Math.min(y0, n[1]); y1 = Math.max(y1, n[1]);
      return;
    }
    n.forEach(walk);
  };
  walk(geom?.coordinates);
  return Number.isFinite(x0) ? [x0, y0, x1, y1] : null;
}

async function loadOverrides() {
  if (!regionName.value || !api()) return;
  const { data } = await api().regions.landmarks(regionName.value);
  if (data?.overrides) (store as any).cityLandmarkOverrides = data.overrides;
}

async function loadList() {
  errorMsg.value = '';
  if (!hasBuildings.value) { items.value = []; return; }
  busy.value = 'list';
  const { data, error } = await api().cities.landmarks({
    buildings: buildingsPayload(), tallest_n: tallestN.value || 0, region: regionName.value || null,
  });
  busy.value = '';
  if (error) { errorMsg.value = error; return; }
  items.value = data.landmarks || [];
  sources.value = data.survey_sources || [];
  idsMissing.value = !!data.ids_missing;
  if (regionName.value) (store as any).cityLandmarkOverrides = data.overrides || {};
  if (selected.value) {
    selected.value = items.value.find((i) => i.osm_id === selected.value.osm_id) || null;
  }
}

function select(it: any) {
  selected.value = it;
  draft.value = draftFromSpec(overrides.value[it.osm_id]);
  previewInfo.value = '';
  hasPreview.value = false;
  (window as any).selectCityBuilding?.(it.index);
}

async function onFile(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0];
  if (!file) return;
  busy.value = 'upload';
  const { data, error } = await api().mesh.upload(file);
  busy.value = '';
  if (error) { errorMsg.value = 'Upload failed: ' + error; return; }
  draft.value = { ...draft.value, upload_id: data.upload_id, filename: data.filename || file.name, format: data.format || '' };
}

async function preview() {
  if (!selected.value?.osm_id) return;
  errorMsg.value = '';
  busy.value = 'preview';
  const spec = specFromDraft(draft.value);
  const { data, error } = await api().cities.landmarkPreview({
    buildings: buildingsPayload(selected.value.bbox), osm_id: selected.value.osm_id,
    override: spec.kind === 'osm' ? null : spec,
  });
  busy.value = '';
  if (error) { errorMsg.value = error; return; }
  const rep = data.report?.landmarks?.[selected.value.osm_id];
  const size = (data.size_mm || []).map((v: number) => v.toFixed(0)).join(' × ');
  previewInfo.value = (rep ? (rep.status === 'applied'
    ? `${rep.kind} applied (${rep.source || rep.kind}, ${rep.replaced_features} OSM feature(s) replaced)`
    : `${rep.kind} not applied: ${rep.reason}`) : 'OSM tags and parts')
    + ` · ${size} mm at ${data.mm_per_m} mm/m · ${data.faces.length / 3} triangles`
    + (data.report?.watertight ? '' : ' · NOT watertight');
  hasPreview.value = true;
  _show(data.vertices, data.faces);
}

async function save() {
  const it = selected.value;
  if (!it?.osm_id) return;
  const spec = specFromDraft(draft.value);
  errorMsg.value = '';
  if (regionName.value) {
    busy.value = 'save';
    const { error } = await api().regions.saveLandmark(regionName.value, it.osm_id, spec);
    busy.value = '';
    if (error) { errorMsg.value = error; return; }
  }
  (store as any).cityLandmarkOverrides = { ...overrides.value, [it.osm_id]: spec };
  (window as any).showToast?.(`Landmark override saved (${overrideLabel(spec)})`, 'success');
}

async function reset() {
  const it = selected.value;
  if (!it?.osm_id) return;
  if (regionName.value) {
    busy.value = 'save';
    await api().regions.deleteLandmark(regionName.value, it.osm_id);
    busy.value = '';
  }
  const next = { ...overrides.value };
  delete next[it.osm_id];
  (store as any).cityLandmarkOverrides = next;
  draft.value = draftFromSpec(undefined);
}

// ── three.js preview ─────────────────────────────────────────────────────────
let _three: any = null;
let _onUp: (() => void) | null = null;

function _ensureViewer() {
  const THREE = (window as any).THREE;
  const el = viewEl.value;
  if (!THREE || !el) return null;
  if (_three && _three.el === el) return _three;
  const w = el.clientWidth || 300;
  const h = 220;
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setSize(w, h);
  renderer.setPixelRatio(window.devicePixelRatio || 1);
  el.innerHTML = '';
  el.appendChild(renderer.domElement);
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x1b1b1b);
  scene.add(new THREE.AmbientLight(0xffffff, 0.45));
  const sun = new THREE.DirectionalLight(0xffffff, 0.75);
  sun.position.set(1, -1.5, 2);
  scene.add(sun);
  const camera = new THREE.PerspectiveCamera(35, w / h, 0.1, 10000);
  camera.up.set(0, 0, 1);
  const state = { az: -0.9, el: 0.6, dist: 150, target: new THREE.Vector3() };
  const place = () => {
    const c = Math.cos(state.el);
    camera.position.set(state.target.x + state.dist * c * Math.cos(state.az),
      state.target.y + state.dist * c * Math.sin(state.az), state.target.z + state.dist * Math.sin(state.el));
    camera.lookAt(state.target);
    renderer.render(scene, camera);
  };
  let drag: [number, number] | null = null;
  renderer.domElement.addEventListener('mousedown', (e: MouseEvent) => { drag = [e.clientX, e.clientY]; });
  _onUp = () => { drag = null; };
  window.addEventListener('mouseup', _onUp);
  renderer.domElement.addEventListener('mousemove', (e: MouseEvent) => {
    if (!drag) return;
    state.az -= (e.clientX - drag[0]) * 0.01;
    state.el = Math.max(0.05, Math.min(1.5, state.el + (e.clientY - drag[1]) * 0.01));
    drag = [e.clientX, e.clientY];
    place();
  });
  renderer.domElement.addEventListener('wheel', (e: WheelEvent) => {
    e.preventDefault();
    state.dist *= e.deltaY > 0 ? 1.1 : 0.9;
    place();
  }, { passive: false });
  _three = { el, THREE, renderer, scene, camera, state, place, mesh: null };
  return _three;
}

function _show(vertices: number[], faces: number[]) {
  // After the next render, so v-show has shown the container.
  void nextTick(() => {
    const v = _ensureViewer();
    if (!v) { previewInfo.value += ' · (three.js unavailable: no 3D view)'; return; }
    const { THREE } = v;
    if (v.mesh) { v.scene.remove(v.mesh); v.mesh.geometry.dispose(); }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3));
    g.setIndex(faces);
    g.computeVertexNormals();
    v.mesh = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ color: 0xd9c9a8, flatShading: true }));
    v.scene.add(v.mesh);
    const b = meshBounds(vertices);
    v.state.target.set(b.center[0], b.center[1], b.center[2]);
    v.state.dist = b.size * 1.9;
    v.place();
  });
}

onBeforeUnmount(() => {
  if (_onUp) window.removeEventListener('mouseup', _onUp);
  _three?.renderer?.dispose?.();
  _three = null;
});

watch(regionName, () => { (store as any).cityLandmarkOverrides = {}; void loadOverrides(); }, { immediate: true });
watch(() => (store as any).osmCityData, () => {
  selected.value = null;
  hasPreview.value = false;
  if (items.value.length) void loadList();
});
</script>

<style scoped>
.lm-head { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-bottom: 4px; }
.lm-muted { font-size: 11px; color: var(--text-dim); }
.lm-warn { font-size: 11px; color: #e67e22; margin: 4px 0; line-height: 1.35; }
.lm-error { font-size: 11px; color: #e08080; margin: 4px 0; }
.lm-num { width: 56px; }
.lm-table { width: 100%; border-collapse: collapse; font-size: 11px; margin-top: 4px; }
.lm-table th { text-align: left; color: #aaa; font-weight: 600; padding: 3px 4px; border-bottom: 1px solid #2b2b2b; }
.lm-table td { padding: 3px 4px; border-bottom: 1px solid #242424; color: #d5d5d5; vertical-align: top; }
.lm-table tbody tr { cursor: pointer; }
.lm-table tbody tr:hover { background: rgba(255, 255, 255, 0.04); }
.lm-table tbody tr.selected { background: rgba(255, 210, 77, 0.14); box-shadow: inset 2px 0 0 #ffd24d; }
.lm-name { font-weight: 600; color: #eee; }
.lm-sub { font-size: 11px; color: #7a7a7a; }
.lm-numcell { text-align: right; font-variant-numeric: tabular-nums; }
.lm-overridden { color: #ffd24d; }
.lm-editor { margin-top: 8px; padding-top: 6px; border-top: 1px solid #2a2a2a; font-size: 11px; }
.lm-editor-title { font-weight: 600; color: #eee; margin-bottom: 4px; }
.lm-kinds { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 4px; }
.lm-row { margin: 4px 0; }
.lm-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 8px; margin: 4px 0; font-size: 11px; color: #bbb; }
.lm-grid label { display: flex; align-items: center; gap: 4px; justify-content: space-between; }
.lm-actions { display: flex; gap: 6px; margin: 6px 0 4px; }
.lm-view { width: 100%; height: 220px; margin-top: 4px; border: 1px solid #2a2a2a; border-radius: 4px; overflow: hidden; }
</style>
