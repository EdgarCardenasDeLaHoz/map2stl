<template>
  <div class="model-score">
    <div class="model-score-head">
      <button id="scoreModelBtn" class="btn btn-secondary btn-sm" :disabled="busy" @click="score"
              title="Score the model's building heights against a registered plate or a lidar nDSM on the same grid (per-building median absolute error, footprint IoU, roof summary)">
        <span class="btn-icon">📏</span> {{ busy ? 'Scoring…' : 'Score this model' }}
      </button>
      <button class="btn btn-secondary btn-sm" :disabled="busy" title="Refresh the reference list for the current bbox" @click="loadReferences">↻</button>
    </div>

    <div class="param-group model-score-row">
      <label for="scoreModelSource">Model</label>
      <select id="scoreModelSource" v-model="source" class="ctrl-input-sm">
        <option value="buildings">Current city buildings (as the City Model extrudes them)</option>
        <option value="stl">An STL file of this region…</option>
      </select>
    </div>
    <div v-if="source === 'stl'" class="param-group model-score-row">
      <input type="file" accept=".stl,.obj" aria-label="Model STL to score" @change="onFile">
      <span class="model-score-muted">{{ stlName || 'spans the current DEM bbox' }}</span>
    </div>

    <div class="param-group model-score-row">
      <label for="scoreModelRef">Against</label>
      <select id="scoreModelRef" v-model="refKey" class="ctrl-input-sm">
        <option v-if="!references.length" value="">— no reference overlaps this bbox —</option>
        <option v-for="r in references" :key="refKeyOf(r)" :value="refKeyOf(r)">
          {{ r.kind === 'pack' ? `${r.city} plate (${r.slug})${r.overlap != null ? ' · ' + Math.round(r.overlap * 100) + '% overlap' : ''}` : r.city }}
        </option>
      </select>
    </div>

    <div v-if="errorMsg" class="model-score-error">{{ errorMsg }}</div>

    <table v-if="rows.length" class="model-score-table" aria-label="Model score">
      <tbody>
        <tr v-for="row in rows" :key="row.label" :title="row.hint">
          <th>{{ row.label }}</th><td>{{ row.value }}</td>
        </tr>
      </tbody>
    </table>
    <div v-if="result?.roofs?.note" class="model-score-muted">{{ result.roofs.note }}</div>
  </div>
</template>

<script setup lang="ts">
/**
 * ModelScorePanel — "Score this model" (F-LANDMARK §6, F-REGION §5).
 *
 * Posts the current city-model buildings (with any Buildings-panel height edits) or an
 * uploaded STL to POST /api/registration/critic/score, against a registered plate pack
 * or the 3DEP nDSM, and shows city2stl.registration.critic's metrics.  Self-contained so
 * the Export tab mounts it with one line.
 */
import { computed, onMounted, ref } from 'vue';
import { buildingsWithOverrides, hasOverrides } from '../../../modules/layers/building-heights.js';

const api = () => (window as any).api;
const appState = () => (window as any).appState || {};

const source = ref<'buildings' | 'stl'>('buildings');
const references = ref<any[]>([]);
const refKey = ref('');
const busy = ref(false);
const errorMsg = ref('');
const result = ref<any>(null);
const uploadId = ref('');
const stlName = ref('');

const refKeyOf = (r: any) => (r.kind === 'pack' ? `pack:${r.slug}` : 'ndsm');
const fmt = (v: any, digits = 1, unit = '') =>
  (typeof v === 'number' && Number.isFinite(v)) ? `${v.toFixed(digits)}${unit}` : '—';

function currentBbox() {
  const b = appState().currentDemBbox || appState().selectedRegion;
  return b && Number.isFinite(b.north) ? { north: b.north, south: b.south, east: b.east, west: b.west } : null;
}

async function loadReferences() {
  const bbox = currentBbox();
  if (!bbox || !(window as any).api) { references.value = []; return; }
  const { data, error } = await api().registration.criticReferences(bbox);
  if (error) { errorMsg.value = error; return; }
  references.value = data.references || [];
  if (!references.value.some((r) => refKeyOf(r) === refKey.value)) {
    refKey.value = references.value.length ? refKeyOf(references.value[0]) : '';
  }
}

async function onFile(e: Event) {
  const input = e.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  busy.value = true;
  const { data, error } = await api().mesh.upload(file);
  busy.value = false;
  if (error) { errorMsg.value = 'Upload failed: ' + error; return; }
  uploadId.value = data.upload_id;
  stlName.value = data.filename;
}

async function score() {
  errorMsg.value = '';
  result.value = null;
  if (!references.value.length) await loadReferences();
  const bbox = currentBbox();
  const ref = references.value.find((r) => refKeyOf(r) === refKey.value);
  if (!ref) { errorMsg.value = 'No reference: load a region that overlaps a registered plate pack.'; return; }

  let model: any;
  if (source.value === 'buildings') {
    const feats = appState().osmCityData?.buildings?.features;
    if (!feats?.length) { errorMsg.value = 'Load the city buildings first (City layer).'; return; }
    const overrides = appState().cityHeightOverrides;
    const features = (hasOverrides(overrides) ? buildingsWithOverrides(feats, overrides) : feats)
      .map((f: any) => ({ type: 'Feature', geometry: f.geometry, properties: { height_m: f.properties?.height_m } }));
    model = { kind: 'buildings', features };
  } else {
    if (!uploadId.value || !bbox) { errorMsg.value = 'Choose an STL of the current region first.'; return; }
    model = { kind: 'stl', upload_id: uploadId.value, bbox };
  }
  const reference = ref.kind === 'pack' ? { kind: 'pack', slug: ref.slug } : { kind: 'ndsm', bbox };

  busy.value = true;
  const { data, error } = await api().registration.criticScore({ reference, model });
  busy.value = false;
  if (error) { errorMsg.value = error; return; }
  result.value = data;
}

const rows = computed(() => {
  const r = result.value;
  if (!r) return [];
  const b = r.buildings || {};
  const f = r.footprint || {};
  const c = r.cells || {};
  const roofs = r.roofs || {};
  const out = [
    { label: 'Buildings scored', value: String(b.n ?? 0), hint: 'Footprints with enough cells on both surfaces' },
    { label: 'Median |height error|', value: fmt(b.median_abs_error_m, 2, ' m'), hint: 'Per-building median absolute height error (the headline)' },
    { label: 'p90 |height error|', value: fmt(b.p90_abs_error_m, 1, ' m'), hint: '90th percentile of the per-building absolute error' },
    { label: 'Mean error (bias)', value: fmt(b.mean_error_m, 1, ' m'), hint: 'Mean(model - reference) per building; positive = model taller' },
    { label: 'Within 2 m / 5 m', value: `${fmt(b.within_2m_pct, 0, '%')} / ${fmt(b.within_5m_pct, 0, '%')}`, hint: 'Share of buildings within 2 m and 5 m' },
    { label: 'Footprint IoU', value: fmt(f.iou, 2), hint: 'Built(model) vs built(reference), > 2 m above ground' },
    { label: 'Precision / recall', value: `${fmt(f.precision, 2)} / ${fmt(f.recall, 2)}`, hint: 'Model built cells the reference confirms / reference built cells the model covers' },
    { label: 'Cell MAE / r', value: `${fmt(c.mae_m, 1, ' m')} / ${fmt(c.pearson_r, 2)}`, hint: 'Per-cell error and correlation on cells both call built' },
    { label: 'Roof shape error', value: fmt(roofs.median_shape_error_m, 2, ' m'), hint: 'Median within-footprint shape error (height minus the building median)' },
    { label: 'Roof relief ref / model', value: `${fmt(roofs.median_relief_ref_m, 1, ' m')} / ${fmt(roofs.median_relief_model_m, 1, ' m')}`, hint: 'Median p90-p10 height inside a footprint' },
    { label: 'Grid', value: `${fmt(r.cell_m, 1, ' m')} cells · ${r.reference?.source || ''}`, hint: 'Reference grid the model was rasterized onto' },
  ];
  if (r.scale?.fitted) {
    out.push({ label: 'Fitted scale', value: fmt(r.scale.m_per_unit, 3, ' m/unit'), hint: 'STL units put in metres by the median ratio (absorbs uniform bias)' });
  }
  return out;
});

onMounted(loadReferences);
</script>

<style scoped>
.model-score { margin-top: 8px; font-size: 11px; }
.model-score-head { display: flex; gap: 6px; }
.model-score-head .btn:first-child { flex: 1; }
.model-score-row { margin: 4px 0; }
.model-score-row select { max-width: 100%; }
.model-score-muted { font-size: 11px; color: var(--text-dim); }
.model-score-error { color: #e08080; margin: 4px 0; }
.model-score-table { width: 100%; border-collapse: collapse; margin-top: 4px; }
.model-score-table th { text-align: left; font-weight: normal; color: #8ea6bf; padding: 1px 4px; }
.model-score-table td { text-align: right; color: #ddd; padding: 1px 4px; font-variant-numeric: tabular-nums; }
</style>
