<template>
  <!-- Pre-flight: POST /api/export/preflight with the body the build would send
       (export-handlers.js:runPreflight). Nothing is built; see
       app/server/core/preflight.py for what is estimated and how. -->
  <CollapsibleSection title="✈ Pre-flight check" wrap-style="margin-bottom:10px;" id="preflightSection">
    <div class="pf-row">
      <select id="preflightFormat" v-model="format" class="ctrl-input-sm" style="width:auto;"
              title="Which build to check: the City Model (with its layers and puzzle) or the Split / Puzzle export">
        <option value="city">City model</option>
        <option value="puzzle">Terrain puzzle</option>
      </select>
      <button id="preflightRunBtn" type="button" class="btn btn-secondary btn-sm" style="flex:1;"
              :disabled="loading" @click="run">
        {{ loading ? '⏳ Checking…' : 'Run pre-flight' }}
      </button>
    </div>
    <div v-if="error" class="pf-error">{{ error }}</div>
    <div v-if="report" id="preflightReport" class="pf-report" :class="{ stale }">
      <div v-if="stale" class="pf-stale">Settings changed since this check — run it again.</div>
      <div class="pf-grid">
        <span>Size</span>
        <span :class="report.fits_bed ? 'ok' : 'warn'">
          {{ report.size_mm.map(v => Math.round(v)).join(' × ') }} mm
          {{ report.fits_bed ? '✓ fits' : '✗ exceeds' }} {{ report.bed_mm.join('×') }} bed
        </span>
        <span>Scale</span>
        <span>1:{{ report.scale.scale_1_to.toLocaleString() }} · vertical {{ report.vertical_exaggeration }}×</span>
        <template v-if="report.puzzle">
          <span>Pieces</span>
          <span>{{ report.puzzle.cols }} × {{ report.puzzle.rows }} ({{ report.puzzle.method }},
            ≤ {{ report.puzzle.largest_piece_mm.join('×') }} mm)</span>
        </template>
        <span>Faces</span><span>~{{ report.faces_est.toLocaleString() }}</span>
        <span>Thinnest</span>
        <span>{{ report.thinnest_feature_mm == null ? '—' : report.thinnest_feature_mm + ' mm' }}</span>
        <span>Tallest spike</span><span>{{ report.tallest_spike_mm }} mm</span>
        <span>Filament</span>
        <span :title="report.estimate.formula">~{{ report.estimate.filament_g }} g PLA
          ({{ report.estimate.printed_cm3 }} cm³ printed)</span>
        <span>Print time</span>
        <span :title="report.estimate.formula">~{{ report.estimate.print_hours }} h (rough)</span>
      </div>
      <table v-if="layerRows.length" class="pf-layers">
        <tr><th>Layer</th><th>shapes</th><th>widened</th><th>capped</th></tr>
        <tr v-for="l in layerRows" :key="l.name">
          <td>{{ l.name }}</td><td>{{ l.polygons }}</td>
          <td :class="{ warn: l.widened }">{{ l.widened || 0 }}</td>
          <td :class="{ warn: l.clamped }">{{ l.clamped || 0 }}</td>
        </tr>
      </table>
      <ul v-if="report.warnings.length" id="preflightWarnings" class="pf-warnings">
        <li v-for="(w, i) in report.warnings" :key="i">⚠ {{ w }}</li>
      </ul>
      <div v-else class="ok" style="margin-top:4px;">✓ No warnings</div>
      <div class="pf-note">Checked in {{ report.seconds }} s · estimates, not a slice</div>
    </div>
  </CollapsibleSection>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import CollapsibleSection from '../shared/CollapsibleSection.vue';

interface LayerFigures { polygons: number; widened?: number; clamped?: number }
interface PreflightReport {
  size_mm: number[]; bed_mm: number[]; fits_bed: boolean;
  scale: { scale_1_to: number }; vertical_exaggeration: number;
  puzzle?: { cols: number; rows: number; method: string; largest_piece_mm: number[] };
  faces_est: number; thinnest_feature_mm: number | null; tallest_spike_mm: number;
  estimate: { filament_g: number; printed_cm3: number; print_hours: number; formula: string };
  layers: Record<string, LayerFigures>; warnings: string[]; seconds: number;
}

const props = defineProps<{ formTick?: number }>();

const format = ref<'city' | 'puzzle'>('city');
const loading = ref(false);
const error = ref('');
const report = ref<PreflightReport | null>(null);
const checkedAt = ref(-1);
const edgesTick = ref(0);
const checkedEdges = ref(0);

// Any Extrude-form change (formTick) or dragged cut makes the shown report stale.
const stale = computed(() => !!report.value
  && (checkedAt.value !== (props.formTick ?? 0) || checkedEdges.value !== edgesTick.value));

const layerRows = computed(() => Object.entries(report.value?.layers || {})
  .map(([name, l]) => ({ name, ...l })));

async function run() {
  const w = window as any;
  loading.value = true;
  error.value = '';
  const tick = props.formTick ?? 0;
  const edges = edgesTick.value;
  try {
    const { data, error: err } = await w.runPreflight(format.value);
    if (err) throw new Error(err);
    report.value = data as PreflightReport;
    checkedAt.value = tick;
    checkedEdges.value = edges;
  } catch (e) {
    error.value = 'Pre-flight failed: ' + (e as Error).message;
  } finally {
    loading.value = false;
  }
}

function onEdges() { edgesTick.value++; }
onMounted(() => window.addEventListener('puzzle-edges-changed', onEdges));
onBeforeUnmount(() => window.removeEventListener('puzzle-edges-changed', onEdges));
</script>

<style scoped>
.pf-row { display: flex; gap: 6px; align-items: center; }
.pf-error { color: #e74c3c; font-size: 11px; margin-top: 4px; }
.pf-report { font-size: 11px; margin-top: 6px; line-height: 1.45; }
.pf-report.stale { opacity: 0.6; }
.pf-stale { color: #e6b422; margin-bottom: 4px; }
.pf-grid { display: grid; grid-template-columns: auto 1fr; gap: 1px 8px; }
.pf-grid > span:nth-child(odd) { color: var(--text-dim); }
.pf-layers { width: 100%; border-collapse: collapse; margin-top: 6px; }
.pf-layers th { color: var(--text-dim); font-weight: normal; text-align: left; }
.pf-layers td, .pf-layers th { padding: 0 4px 0 0; }
.pf-warnings { margin: 6px 0 0; padding-left: 0; list-style: none; color: #e67e22; }
.pf-note { color: var(--text-dim); font-size: 10px; margin-top: 4px; }
.ok { color: #7c7; }
.warn { color: #e67e22; }
</style>
